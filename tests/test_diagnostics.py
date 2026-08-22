from basemode.exceptions import EmptyCompletionError

from basemode_loom.diagnostics import provider_diagnostic


def test_provider_diagnostic_surfaces_empty_completion_finish_reason():
    error = EmptyCompletionError(
        model="moonshot/kimi-k3", strategy="prefill", finish_reason="content_filter"
    )
    diagnostic = provider_diagnostic(error)
    assert diagnostic.category == "empty_response"
    assert diagnostic.finish_reason == "content_filter"
    assert diagnostic.status is None


def test_provider_diagnostic_empty_completion_without_finish_reason():
    error = EmptyCompletionError(model="gpt-4o-mini", strategy="system")
    diagnostic = provider_diagnostic(error)
    assert diagnostic.category == "empty_response"
    assert diagnostic.finish_reason is None
