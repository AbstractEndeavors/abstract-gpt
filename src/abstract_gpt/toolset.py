"""Optional abstract_toolserver /gpt/* category."""
from . import actions


def get_toolset():
    return {"gpt": {
        "state": actions.build_state,
        "oauth_status": actions.auth_status,
        "oauth_solution": actions.auth_solution,
        "login_start": actions.login_start,
        "login_poll": actions.login_poll,
        "save_template": actions.save_template,
        "restore": actions.restore,
        "set_model": actions.set_model,
    }}


TOOLSET = get_toolset()
