class TenantNotFoundError(Exception):
    pass


class SessionExpiredError(Exception):
    pass


class HITLTimeoutError(Exception):
    pass


class RecipeSearchError(Exception):
    pass


class AgentError(Exception):
    pass
