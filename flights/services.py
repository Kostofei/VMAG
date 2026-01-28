import copy
import asyncio
from decimal import Decimal

from django.db import transaction
from asgiref.sync import sync_to_async
from datetime import datetime
from playwright.async_api import async_playwright, Browser, Page, Playwright, Locator

from flights.decorators import timeit

ROUTE_TYPES = [
    ('one-way', 'one-way'),
    ('return', 'roundtrip'),
    ('multi-city', 'multi-city'),
]


class FlightParser:
    """Сервис для автоматизации поиска билетов."""

    def __init__(self):
        self.base_url = "https://ctb.business-class.com"

    @staticmethod
    async def _get_browser(playwright: Playwright) -> Browser:
        """Инициализация браузера."""
        return await playwright.chromium.launch(
            headless=True,
        )

    @staticmethod
    async def _disable_animations(page):
        """Отключает анимации на странице для стабильности автоматизации."""
        await page.add_style_tag(content="""
            *, *::before, *::after {
                transition-duration: 0s !important;
                transition-delay: 0s !important;
                animation-duration: 0s !important;
                animation-delay: 0s !important;
                scroll-behavior: auto !important;
            }
        """)

    @staticmethod
    def _determine_trip_type(legs: list) -> str:
        """Автоматически определяет тип поездки по количеству сегментов."""
        count = len(legs)

        if count == 1:
            return 'one-way'

        if count == 2:
            leg1, leg2 = legs[0], legs[1]
            # Проверяем, является ли это возвратом в точку старта
            if leg1['origin'] == leg2['destination'] and leg1['destination'] == leg2['origin']:
                return 'return'

        return 'multi-city'

    @staticmethod
    def _format_date(date_str: str) -> str:
        """Приводит дату к формату YYYY-MM-DD."""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            pass

        try:
            return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Неподдерживаемый формат даты: {date_str}")

    @staticmethod
    async def _scroll_page(page: Page) -> None:
        """Прокручивает страницу до самого низа."""

        print("Начинаем прокрутку для загрузки всех билетов...")

        # Настройки
        max_retries = 3  # Сколько раз ждем, если новые билеты не появились
        retry_interval = 1000  # Пауза между скроллами (мс)

        last_count = 0
        no_change_counter = 0

        while True:
            # Прокручиваем страницу вниз (эмуляция нажатия End)
            await page.keyboard.press("End")

            # Ждем загрузки
            await page.wait_for_timeout(retry_interval)

            # Считаем количество карточек билетов
            current_count = await page.locator(".ticket").count()

            # print(f"Загружено билетов: {current_count}")

            # 4. Логика выхода
            if current_count == last_count:
                no_change_counter += 1
                # print(f"  -> Ничего не изменилось ({no_change_counter}/{max_retries})")

                if no_change_counter >= max_retries:
                    print(f"Скролл завершен. Всего найдено: {current_count}")
                    break
            else:
                # Если нашли новые билеты, сбрасываем счетчик попыток
                no_change_counter = 0
                last_count = current_count

    @staticmethod
    @sync_to_async
    def _save_flights_to_db(flights_data: list) -> None:
        """Сохранение билетов и сегментов согласно структуре моделей Django."""

        if not flights_data:
            print("Нет данных для сохранения.")
            return

        from .models import Ticket, FlightSegment

        tickets_processed = 0
        flights_data_copy = copy.deepcopy(flights_data)

        try:
            with transaction.atomic():
                for ticket_dict in flights_data_copy:

                    segments_list = ticket_dict.pop('segments', [])

                    # Создаем или обновляем Ticket
                    ticket_obj, created = Ticket.objects.update_or_create(
                        ticket_uid=ticket_dict.get('ticket_uid'),
                        defaults={
                            'validating_airline': ticket_dict.get('validating_airline'),
                            'price': Decimal(str(ticket_dict.get('price', 0))),
                            'route_type': ticket_dict.get('route_type'),
                        }
                    )

                    # Очищаем старые сегменты для этого билета (если он обновляется)
                    FlightSegment.objects.filter(ticket=ticket_obj).delete()

                    # Подготавливаем новые сегменты
                    segments_to_create = []
                    for seg_data in segments_list:
                        segments_to_create.append(
                            FlightSegment(
                                ticket=ticket_obj,  # Django сам подставит ticket_uid
                                operating_airline=seg_data.get('operating_airline'),
                                departure=seg_data.get('departure'),
                                departure_date=seg_data.get('departure_date'),
                                arrival=seg_data.get('arrival'),
                                arrival_date=seg_data.get('arrival_date'),
                                order=seg_data.get('order')
                            )
                        )

                    # Массовое создание сегментов
                    if segments_to_create:
                        FlightSegment.objects.bulk_create(segments_to_create)

                    tickets_processed += 1

            # print(f"Успешно обработано {tickets_processed} билетов в базе данных.")

        except Exception as e:
            print(f"Критическая ошибка при сохранении в БД: {e}")

    @staticmethod
    def _clean_datetime(date_str: str, time_str: str):
        """Вспомогательная функция для сборки datetime объектов."""
        dt = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%a, %b %d, %Y %I:%M %p")
        return dt.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    async def _wait_for_content(page: Page) -> bool:
        """
        Ждет загрузки контента: либо появления билетов, либо сообщения, что рейсов нет.
        Возвращает True, если билеты найдены, и False, если рейсов нет или случился тайм-аут.
        """

        # Селектор ищет блок прогресс-бара, внутри которого есть наш финальный текст
        final_status_selector = ".ui-progress-bar:has-text('You got our best deals')"
        nothing_found_selector = ".nothing-found"

        try:
            # Ждем загрузки всех билетов ИЛИ блока "Nothing found"
            result = await page.wait_for_selector(
                f"{final_status_selector}, {nothing_found_selector}",
                timeout=60000
            )

            # Проверяем, что именно сработало
            content = await result.inner_text()
            if "best deals" in content:
                print("Все билеты загружены.")
                return True
            else:
                print("Инфо: Рейсов не найдено.")
                return False

        except Exception as e:
            print(f"Ошибка при ожидании контента: {e}")
            return False

    @staticmethod
    @timeit
    async def _expand_all_tickets(page: Page) -> None:
        """Раскрывает всех ненажатых билетов на странице."""
        print("Начинаем раскрывать билеты")
        # Находим все ненажатые билеты
        untouched_tickets = await page.query_selector_all('.ticket:not(.ticket--expanded)')

        if len(untouched_tickets) == 0:
            print("Все билеты уже раскрыты.")
            return

        clicked_tickets_count = await page.evaluate('''() => {
            // Находим все не нажатые билеты
            const untouchedTickets = Array.from(
                document.querySelectorAll('.ticket:not(.ticket--expanded)')
            ).filter(ticket => {
                // Убедимся, что у билета нет скрытых деталей
                return !ticket.querySelector('.ticket-details, .ticket-details--hidden');
            });

            // Кликаем по кнопке раскрытия в каждом ненажатом билете
            untouchedTickets.forEach(ticket => {
                const toggleButton = ticket.querySelector('[data-test-id="ticket-toggle-details"]');
                if (toggleButton) {
                    toggleButton.click();
                }
            });

            // Возвращаем количество нажатых билетов (опционально)
            return untouchedTickets.length;
        }''')

        # Ожидание, пока все билеты раскроются
        await page.wait_for_function(
            f'''() => {{
                const expandedTickets = document.querySelectorAll('.ticket--expanded');
                return expandedTickets.length >= {clicked_tickets_count};
            }}''',
            timeout=50000
        )

        print(f"Раскрыто билетов: {clicked_tickets_count}")
        return

    async def _extract_ticket_data(self, ticket: Locator, search_data: dict) -> dict:
        try:
            # Забираем данные из браузера одним куском (JS)
            raw = await ticket.evaluate("""
                                        (ticketEl) => {
                                            const groups = Array.from(ticketEl.querySelectorAll(".ticket-details-group"));
                                            const segments = [];

                                            groups.forEach(group => {
                                                // Берем чистую дату из заголовка группы
                                                const groupDate = group.querySelector(".ticket-details-group__title-date")?.innerText?.trim() || "";

                                                // Ищем строку прибытия, которая НЕ содержит 'Duration'
                                                const summaryItems = Array.from(group.querySelectorAll(".ticket-details-group__summary-item"));
                                                const arrivalLine = summaryItems.find(el => el.innerText.includes("Arrives:"))?.innerText || "";

                                                const flights = Array.from(group.querySelectorAll(".ticket-details-flight"));
                                                flights.forEach((f) => {
                                                    if (!f.querySelector(".ticket-details-flight__wrap")) return;

                                                    const times = Array.from(f.querySelectorAll(".ticket-details-flight__time"));
                                                    const airports = Array.from(f.querySelectorAll(".ticket-details-flight__airport"));

                                                    segments.push({
                                                        airline: f.querySelector(".ticket-details-flight__airlines b")?.innerText?.trim() || "",
                                                        dep_iata: airports[0]?.innerText?.trim() || "",
                                                        arr_iata: airports[airports.length - 1]?.innerText?.trim() || "",
                                                        dep_time: times[0]?.innerText?.trim() || "",
                                                        arr_time: times[times.length - 1]?.innerText?.trim() || "",
                                                        dep_date: groupDate,
                                                        arr_date_raw: arrivalLine,
                                                    });
                                                });
                                            });

                                            return {
                                                airline: ticketEl.querySelector(".ticket__airlines-name")?.innerText?.trim() || "",
                                                uid: ticketEl.querySelector(".ticket-details__trip")?.innerText?.trim() || "",
                                                price: ticketEl.querySelector(".ticket__total-price span")?.innerText?.trim() || "",
                                                segments: segments
                                            };
                                        }
                                        """)

            # Подготовка данных для функции _clean_datetime
            processed_segments = []
            for idx, s in enumerate(raw['segments']):

                # Очистка от "Arrives:" и "Duration"
                arr_date_val = s['arr_date_raw'].replace("Arrives:", "")
                if "Duration" in arr_date_val:
                    arr_date_val = arr_date_val.split("Duration")[0]

                arr_date_val = arr_date_val.strip()

                try:
                    dep_dt = self._clean_datetime(s['dep_date'], s['dep_time'])
                    arr_dt = self._clean_datetime(arr_date_val, s['arr_time'])
                except Exception as e:
                    print(f"Ошибка формата даты в сегменте {idx}: {e} (Data: {arr_date_val})")
                    continue

                processed_segments.append({
                    "operating_airline": s['airline'].rsplit(' ', 1)[0],
                    "departure": s['dep_iata'][-4:-1],
                    "departure_date": dep_dt,
                    "arrival": s['arr_iata'][-4:-1],
                    "arrival_date": arr_dt,
                    "order": idx,
                })

            return {
                "validating_airline": raw['airline'],
                "ticket_uid": raw['uid'].replace("Ticket ID", "").split("Share")[0].strip(),
                "price": float(raw['price'].replace("$", "").replace(",", "").strip()),
                "route_type": self._determine_trip_type(search_data['legs']),
                "segments": processed_segments
            }

        except Exception as e:
            print(f"Ошибка при парсинге карточки: {e}")
            return {}

    def _process_segment_data(self, raw_seg: dict) -> dict:
        """Чистим данные, полученные из JS."""
        # Чистим авиакомпанию
        op_airline = raw_seg['operating_airline_raw'].strip().rsplit(' ', 1)[0]

        # Извлекаем IATA коды
        dep_iata = raw_seg['departure_raw'].strip()[-4:-1]
        arr_iata = raw_seg['arrival_raw'].strip()[-4:-1]

        # Форматируем даты
        departure_date = self._clean_datetime(raw_seg['dep_date'], raw_seg['dep_time'])

        arr_date_clean = raw_seg['arr_date_raw'].replace("Arrives:", "").strip()
        arrival_date = self._clean_datetime(arr_date_clean, raw_seg['arr_time'])

        return {
            "operating_airline": op_airline,
            "departure": dep_iata,
            "departure_date": departure_date,
            "arrival": arr_iata,
            "arrival_date": arrival_date,
            "order": raw_seg['order'],
        }

    @timeit
    async def _process_chunks(self, items: list, search_data: dict, chunk_size: int = 10) -> list:
        """Универсальный метод для параллельной обработки списка объектов пачками."""
        all_results = []
        total_count = len(items)

        for i in range(0, total_count, chunk_size):
            chunk = items[i:i + chunk_size]

            # Создаем задачи для текущей пачки
            tasks = [self._extract_ticket_data(item, search_data) for item in chunk]

            # Выполняем пачку параллельно
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Собираем только валидные результаты
            for res in chunk_results:
                if isinstance(res, dict) and res:
                    all_results.append(res)
                elif isinstance(res, Exception):
                    print(f"Ошибка при обработке объекта: {res}")

            # print(f"--- Обработано объектов: {len(all_results)} из {total_count} ---")

        return all_results

    def _construct_search_url(self, search_data: dict) -> str:
        """Генерации URL."""
        legs = search_data['legs']
        trip_type = self._determine_trip_type(legs)

        # Формируем строку пассажиров (1:0:0)
        passengers = f"{search_data.get('ADT', 1)}:{search_data.get('CNN', 0)}:{search_data.get('INF', 0)}"

        # Получаем код кабины (Y/C/F)
        cabin_code = search_data.get('cabin', 'C')

        if trip_type == 'one-way':
            leg = legs[0]
            route = f"{leg['origin']}-{leg['destination']}"
            date = self._format_date(leg['date'])
            return f"{self.base_url}/result/{trip_type}/{route}/{date}/{cabin_code}/{passengers}"

        elif trip_type == 'return':
            leg1 = legs[0]
            leg2 = legs[1]

            route = f"{leg1['origin']}-{leg1['destination']}:{leg2['origin']}-{leg2['destination']}"
            dates = f"{self._format_date(leg1['date'])}:{self._format_date(leg2['date'])}"
            cabins = f"{cabin_code}:{cabin_code}"

            return f"{self.base_url}/result/{trip_type}/{route}/{dates}/{cabins}/{passengers}"

        else:  # multi-city

            # Собираем список маршрутов ["JFK-LHR", "LHR-DXB", ...]
            routes_list = [f"{leg['origin']}-{leg['destination']}" for leg in legs]
            routes_str = ":".join(routes_list)

            # Собираем список дат ["2026-01-01", "2026-01-05", ...]
            dates_list = [self._format_date(leg['date']) for leg in legs]
            dates_str = ":".join(dates_list)

            # Собираем кабины ["C", "C", "C"]
            cabins_list = [cabin_code] * len(legs)
            cabins_str = ":".join(cabins_list)

            return f"{self.base_url}/result/{trip_type}/{routes_str}/{dates_str}/{cabins_str}/{passengers}"

    async def _navigate_to_results(self, page: Page, search_data: dict) -> None:
        """
        Генерирует прямой URL, инициализирует сессию на главной
        и переходит к результатам поиска.
        """

        # Генерируем URL
        search_url = self._construct_search_url(search_data)
        print(f"URL: {search_url}")

        # Инициализация сессии
        print("Разогрев сессии на главной странице...")
        await page.goto(self.base_url, wait_until="domcontentloaded")

        # Переход по сгенерированному URL к результатам
        print("Переход к результатам поиска...")
        await page.goto(search_url, wait_until="domcontentloaded")

        # Закрываем GDPR, чтобы он не перекрывал элементы
        gdpr_button = page.locator('[data-qa="gdpr-popup_1_close"]')

        try:
            # Ждем появления кнопки максимум 1 секунду
            if await gdpr_button.is_visible(timeout=1000):
                await gdpr_button.click()
                print("GDPR окно закрыто.")
        except Exception:
            print("GDPR окно не обнаружено, продолжаем...")

    @timeit
    async def _parse_results(self, page: Page, search_data: dict) -> list:
        """Главный метод управления парсингом."""

        # Ждем контент
        if not await self._wait_for_content(page):
            return []

        # Скроллим до упора
        await self._scroll_page(page)

        # Раскрываем все билеты с помощью статического метода
        await self._expand_all_tickets(page)

        # 3. Собираем локаторы всех билетов
        tickets_locator = page.locator(".ticket:not(.ticket--placeholder)")
        tickets = await tickets_locator.all()

        if not tickets:
            print("Билеты не найдены.")
            return []

        print(f"Все билеты найдены и раскрыты ({len(tickets)}).")

        # Обработка данных
        results = await self._process_chunks(tickets, search_data, chunk_size=25)

        return results

    async def run(self, search_data: dict):
        """Точка входа для запуска процесса парсинга."""
        async with async_playwright() as playwright:
            browser = await self._get_browser(playwright)
            context = await browser.new_context()
            page = await context.new_page()

            # Отключаем анимации
            await self._disable_animations(page)

            try:
                # Инициализируем сессию через главную страницу и переходим по прямому URL к результатам
                await self._navigate_to_results(page, search_data)

                # Парсим (внутри уже есть скролл)
                flights_list = await self._parse_results(page, search_data)

                if len(flights_list) > 0:
                    # Сохраняем в БД (это синхронная операция Django)
                    await self._save_flights_to_db(flights_list)

                    print(f"Успешно сохранено {len(flights_list)} рейсов.")
                    return flights_list

                return "No flights found matching your search"

            except Exception as e:
                return f"Ошибка: {e}"
            finally:
                await browser.close()
