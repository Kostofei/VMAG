def custom_postprocessing_hook(result, generator, **kwargs):
    # Текст, который мы ищем
    search_text = "A unique integer value identifying this"
    # Текст, на который меняем
    replacement_text = "Уникальный идентификатор (ID)"

    def replace_description(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == 'description' and isinstance(value, str) and search_text in value:
                    obj[key] = replacement_text
                else:
                    replace_description(value)
        elif isinstance(obj, list):
            for item in obj:
                replace_description(item)

    replace_description(result)
    return result