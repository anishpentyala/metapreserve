def dynamic_metadata(settings, project):
    return {
        "authors": [{"name": "Added, Author", "email": "added@example.com"}],
        "maintainers": [{"email": "extra-maintainer@example.com"}],
    }


def dynamic_wheel(settings):
    return {"authors": True, "maintainers": True}
