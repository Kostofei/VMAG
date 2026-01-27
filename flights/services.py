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
    """Сервис для автоматизации поиска билетов на сайте ctb.business-class.com."""

    def __init__(self):
        self.base_url = "https://ctb.business-class.com"

    @staticmethod
    async def _get_browser(playwright: Playwright) -> Browser:
        """Инициализация браузера."""
        return await playwright.chromium.launch(
            headless=False,
            # headless=True,
            # slow_mo=1000  # Замедляем на 1 сек, чтобы видеть каждое действие
        )

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

            print(f"Успешно обработано {tickets_processed} билетов в базе данных.")

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
                timeout=30000
            )

            # Проверяем, что именно сработало
            content = await result.inner_text()
            if "best deals" in content:
                print("Успех: Все билеты загружены.")
                return True
            else:
                print("Инфо: Рейсов не найдено.")
                return False

        except Exception as e:
            print(f"Ошибка при ожидании контента: {e}")
            return False

    async def _extract_ticket_data(self, ticket: Locator, search_data: dict) -> dict:
        """Парсит одну карточку билета и возвращает список её сегментов."""
        try:
            # Открываем детали

            details_box = ticket.locator(".ticket-details")
            await details_box.wait_for(state="visible", timeout=2000)

            # Собираем общие для всей карточки данные
            validating_airline = await ticket.locator(".ticket__airlines-name").text_content()
            ticket_uid_raw = await ticket.locator(".ticket-details__trip").inner_text()
            ticket_uid = ticket_uid_raw.replace("Ticket ID", "").split("Share")[0].strip()

            price_text = await ticket.locator(".ticket__total-price span").first.text_content()
            price = float(price_text.replace("$", "").replace(",", "").strip())

            route_type = self._determine_trip_type(search_data['legs'])

            type_mapping = {
                'one-way': 'one_way',
                'return': 'roundtrip',
                'multi-city': 'multi_city'
            }

            # Перебираем все полеты в карточке
            segments = []
            flights = await details_box.locator(".ticket-details-flight").all()

            for index, flight_locator in enumerate(flights):
                # Проверяем, не является ли этот блок просто плашкой "пересадка"
                if await flight_locator.locator(".ticket-details-flight__wrap").count() > 0:
                    segment_data = await self._parse_segment(flight_locator, order=index)
                    segments.append(segment_data)

            return {
                "validating_airline": validating_airline,
                "ticket_uid": ticket_uid,
                "price": price,
                "route_type": type_mapping.get(route_type),
                "segments": segments
            }

        except Exception as e:
            print(f"Ошибка при парсинге карточки: {e}")
            return {}

    async def _parse_segment(self, flight: Locator, order: int) -> dict:
        """Извлекает данные конкретного перелета из группы деталей."""

        operating_airline_raw = await flight.locator(
            ".ticket-details-flight__airlines:not([class*='mobile']) b").text_content()
        operating_airline = operating_airline_raw.strip().rsplit(' ', 1)

        departure_raw = await flight.locator(".ticket-details-flight__airport").first.text_content()
        arrival_raw = await flight.locator(".ticket-details-flight__airport").last.text_content()

        dep_time = await flight.locator(".ticket-details-flight__time").first.text_content()
        dep_date = await flight.locator(
            "xpath=./ancestor::div[contains(@class, 'ticket-details-group')]//div[contains(@class, 'ticket-details-group__title-date')]"
        ).first.inner_text()

        arr_time = await flight.locator(".ticket-details-flight__time").last.text_content()
        arr_date_raw = await flight.locator(
            "xpath=./ancestor::div[contains(@class, 'ticket-details-group')]//div[contains(@class, 'ticket-details-group__summary-item')][contains(., 'Arrives:')]"
        ).first.inner_text()
        arr_date = arr_date_raw.replace("Arrives:", "").strip()

        return {
            "operating_airline": operating_airline[0],
            "departure": departure_raw.strip()[-4:-1],
            "departure_date": self._clean_datetime(dep_date, dep_time),
            "arrival": arrival_raw.strip()[-4:-1],
            "arrival_date": self._clean_datetime(arr_date, arr_time),
            "order": order,
        }

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
        print(f"Generated URL: {search_url}")

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

        # Отключаем анимации, чтобы все раскрывалось мгновенно
        await page.add_style_tag(content="""
                *, *::before, *::after {
                    transition-duration: 0s !important;
                    transition-delay: 0s !important;
                    animation-duration: 0s !important;
                    animation-delay: 0s !important;
                }
            """)

        # Ждем контент
        if not await self._wait_for_content(page):
            return []

        # Скроллим до упора
        await self._scroll_page(page)

        while True:
            print("Раскрываю все билеты...")
            buttons = await page.locator(".ticket:not(.ticket--expanded) .ticket__preview").all()
            print(f"Найдено карточек для раскрытия: {len(buttons)}")

            # --------------------------------------------------------------------
            untouched_tickets_count = await page.evaluate('''() => {
                // Находим все ненажатые билеты
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

            print(f"Количество не нажатых билетов: {untouched_tickets_count}")

            # 2. Ожидаем, пока все билеты не раскроются
            # Ожидание появления класса ticket--expanded у всех ранее ненажатых билетов
            await page.wait_for_selector(
                f'.ticket--expanded:nth-child({untouched_tickets_count})',
                state="visible",
                timeout=50000  # Таймаут в миллисекундах (50 секунд)
            )
            break

            # --------------------------------------------------------------------
            # Запускаем клики почти одновременно (небольшими пачками)
            # Если нажать сразу 200 — браузер может «подвиснуть», поэтому жмем по 10 за раз

            # reversed_buttons = buttons[::-1]
            # chunk_size = 3
            # for i in range(0, len(reversed_buttons), chunk_size):
            #     chunk = reversed_buttons[i:i + chunk_size]
            #     # Создаем список задач на клик для текущей пачки
            #     tasks = [btn.click(force=True, no_wait_after=True, timeout=1000) for btn in chunk]
            #     # Выполняем пачку кликов параллельно
            #     await asyncio.gather(*tasks, return_exceptions=True)
            #     # Крошечная пауза, чтобы анимация раскрытия началась
            #     await asyncio.sleep(1)
            #
            # tickets_locator = await page.locator(".ticket:not(.ticket--placeholder)").all()
            # print(f"{len(tickets_locator)} / {len(buttons)}")
            #
            # if len(buttons) == 0:
            #     break

        print("Все клики отправлены. Ожидаем отрисовку деталей...")

        # Ждем, пока последний билет в списке станет раскрытым
        # Если последний раскрылся — значит, браузер дошел до конца очереди
        # try:
        #     last_details = page.locator(".ticket:not(.ticket--placeholder)").last.locator(".ticket-details")
        #     await last_details.wait_for(state="visible", timeout=10000)
        #     print("Отрисовка завершена успешно.")
        # except Exception as e:
        #     print(f"Превышено время ожидания отрисовки: {e}. Пробую продолжать...")
        #     await asyncio.sleep(2)  # Запасная пауза на всякий случай

        # 3. Собираем локаторы всех билетов
        tickets_locator = page.locator(".ticket:not(.ticket--placeholder)")
        tickets = await tickets_locator.all()
        count = len(tickets)
        results = []
        print(f"--- Найдено раскрытых карточек - {count} ---")

        # print("--- HTML КОД ПЕРВОГО БИЛЕТА ---")
        # html_content = await tickets_locator.first.evaluate("el => el.outerHTML")
        # print(html_content)
        #
        # await page.wait_for_timeout(5000)

        # 4. Параллельный сбор пачками (по 50 штук), чтобы не вешать браузер
        chunk_size = 25
        for i in range(0, count, chunk_size):
            chunk = tickets[i:i + chunk_size]
            tasks = [self._extract_ticket_data(t, search_data) for t in chunk]

            # Запускаем пачку параллельно
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Фильтруем успешные ответы
            for res in chunk_results:
                if isinstance(res, dict) and res:
                    results.append(res)

            print(f"--- Обработано билетов: {len(results)} из {count} ---")

        return results

    async def run(self, search_data: dict):
        """Точка входа для запуска процесса парсинга."""
        async with async_playwright() as playwright:
            browser = await self._get_browser(playwright)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                # Инициализируем сессию через главную страницу и переходим по прямому URL к результатам
                await self._navigate_to_results(page, search_data)

                # Парсим (внутри уже есть скролл)
                flights_list = await self._parse_results(page, search_data)

                if len(flights_list) > 0:
                    # Сохраняем в БД (это синхронная операция Django)
                    await self._save_flights_to_db(flights_list)

                    print(f"Успешно собрано {len(flights_list)} рейсов.")
                    return flights_list

                return "No flights found matching your search"

            except Exception as e:
                return f"Ошибка: {e}"
            finally:
                await browser.close()
