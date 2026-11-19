def dynamic_metadata(settings, project):
    return {"dependencies": ["typing-extensions>=4"]}


def dynamic_wheel(settings):
    return {"dependencies": True}
