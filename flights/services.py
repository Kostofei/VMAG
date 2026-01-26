import asyncio
from asgiref.sync import sync_to_async
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Browser, Page, Playwright, Locator

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
            # headless=False,
            headless=True,
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
        """Сохранение списка рейсов в базу данных Django."""
        if not flights_data:
            print("Нет данных для сохранения.")
            return

        from .models import Flight  # Импорт внутри, чтобы избежать циклической зависимости

        flights_to_create = []
        for item in flights_data:
            flights_to_create.append(Flight(**item))

        created_objects = Flight.objects.bulk_create(flights_to_create, ignore_conflicts=True)
        print(f"Успешно сохранено {len(created_objects)} рейсов в БД.")
        return

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
            await ticket.locator(".ticket__preview").click()
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
                    segment_data = await self._parse_segment(flight_locator, index)
                    segments.append(segment_data)
            # groups = ticket.locator(".ticket-details-group")
            # for j in range(await groups.count()):
            #     segment_data = await self._parse_segment(groups.nth(j), order=j)
            #     segments.append(segment_data)

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

    async def _parse_segment(self, group: Locator, order: int) -> dict:
        """Извлекает данные конкретного перелета из группы деталей."""

        operating_airline_raw = await group.locator(".ticket-details-flight__airlines:not([class*='mobile']) b").text_content()
        operating_airline = operating_airline_raw.strip().rsplit(' ', 1)

        departure_raw = await group.locator(".ticket-details-flight__airport").first.text_content()
        arrival_raw = await group.locator(".ticket-details-flight__airport").last.text_content()

        dep_time = await group.locator(".ticket-details-flight__time").first.text_content()
        # dep_date = await group.locator(".ticket-details-group__title-date").inner_text()
        dep_date = await group.locator(
            "xpath=./ancestor::div[contains(@class, 'ticket-details-group')]//div[contains(@class, 'ticket-details-group__title-date')]"
        ).first.inner_text()

        arr_time = await group.locator(".ticket-details-flight__time").last.text_content()
        # arr_summary = await group.locator(".ticket-details-group__summary-item:has-text('Arrives:')").inner_text()
        # arr_date = arr_summary.replace("Arrives:", "").strip()
        arr_date_raw = await group.locator(
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

        tickets_locator = page.locator(".ticket:not(.ticket--placeholder)")
        count = await tickets_locator.count()
        results = []

        for i in range(count):
            if (i + 1) % 10 == 0:
                print(f"--- Обработано билетов: {i + 1} из {count} ---")

            ticket_data = await self._extract_ticket_data(tickets_locator.nth(i), search_data)
            if ticket_data:
                results.append(ticket_data)  # Добавляем список сегментов

        return results

    async def run(self, search_data: dict):
        """Точка входа для запуска процесса парсинга."""
        async with async_playwright() as playwright:
            browser = await self._get_browser(playwright)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                # 1. Инициализируем сессию через главную страницу и переходим по прямому URL к результатам
                await self._navigate_to_results(page, search_data)

                # 2. Парсим (внутри уже есть скролл)
                flights_list = await self._parse_results(page, search_data)

                if len(flights_list) > 0:
                    # 3. Сохраняем в БД (это синхронная операция Django)
                    # await self._save_flights_to_db(flights_list)

                    print(f"Успешно собрано {len(flights_list)} рейсов.")
                    return flights_list

                return "No flights found matching your search"

            except Exception as e:
                return f"Ошибка: {e}"
            finally:
                await browser.close()
