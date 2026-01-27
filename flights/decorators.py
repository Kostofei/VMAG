import time
import asyncio
from functools import wraps


def timeit(func):
    """
    Асинхронный декоратор для измерения времени выполнения функции.

    Аргументы:
        func (function): Асинхронная функция, время выполнения которой нужно измерить.

    Возвращает:
        function: Обёрнутая асинхронная функция с измерением времени выполнения.
    """

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)  # Ожидаем выполнение корутины
            end_time = time.time()
            print(f'Функция {func.__name__} выполнилась за {end_time - start_time:.4f} сек.')
            return result
        except Exception as e:
            end_time = time.time()
            print(f'Функция {func.__name__} завершилась с ошибкой за {end_time - start_time:.4f} сек.')
            raise e

    # Поддержка как синхронных, так и асинхронных функций
    def wrapper(*args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            return async_wrapper(*args, **kwargs)
        else:
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            print(f'Синхронная функция {func.__name__} выполнилась за {end_time - start_time:.4f} сек.')
            return result

    return wrapper