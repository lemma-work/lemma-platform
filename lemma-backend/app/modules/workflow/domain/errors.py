"""Domain errors for workflow module."""

from app.core.domain.errors import DomainError


class WorkflowDomainError(DomainError):
    """Base error for workflow module."""

    def __init__(
        self,
        message: str,
        code: str = "WORKFLOW_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message=message, code=code, status_code=status_code)


class WorkflowConflictError(WorkflowDomainError):
    def __init__(self, message: str):
        super().__init__(message=message, code="WORKFLOW_CONFLICT", status_code=409)


class WorkflowAlreadyInstalledError(WorkflowConflictError):
    def __init__(self, message: str):
        super().__init__(message=message)


class WorkflowValidationError(WorkflowDomainError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="WORKFLOW_VALIDATION_ERROR",
            status_code=400,
        )


class GraphValidationError(WorkflowDomainError):
    """The workflow graph is invalid. Raised at save time, never at run time."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(
            message="Workflow graph is invalid: " + "; ".join(issues),
            code="WORKFLOW_GRAPH_INVALID",
            status_code=422,
        )


class ExpressionSyntaxError(WorkflowDomainError):
    """A JMESPath expression failed to compile."""

    def __init__(self, expression: str, detail: str):
        self.expression = expression
        super().__init__(
            message=f"Invalid expression '{expression}': {detail}",
            code="WORKFLOW_EXPRESSION_INVALID",
            status_code=422,
        )


class ContextPathError(WorkflowDomainError):
    """A required context path resolved to nothing at run time."""

    def __init__(self, expression: str, detail: str | None = None):
        self.expression = expression
        message = f"Context path '{expression}' resolved to nothing"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(
            message=message,
            code="WORKFLOW_CONTEXT_PATH_MISSING",
            status_code=422,
        )


class NodeExecutionError(WorkflowDomainError):
    """A node failed to execute. Fails the run with the node identified."""

    def __init__(self, node_id: str, message: str):
        self.node_id = node_id
        super().__init__(
            message=f"Node '{node_id}': {message}",
            code="WORKFLOW_NODE_EXECUTION_FAILED",
            status_code=422,
        )


class WorkflowAccessDeniedError(WorkflowDomainError):
    def __init__(self, message: str = "Access denied"):
        super().__init__(
            message=message,
            code="WORKFLOW_ACCESS_DENIED",
            status_code=403,
        )


class DecisionStepError(WorkflowDomainError):
    """A DECISION node's question was refused, so the run cannot go on.

    The decider does not exist, the run's person may not ask it, or the answer
    has no question to branch on. Each is fixed in the workflow or the pod, not
    by asking again, so the run fails with the reason instead of retrying.
    """

    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="WORKFLOW_DECISION_FAILED",
            status_code=422,
        )
