# Compaction invocation failures

The Runtime `compact_context` wrapper chooses whether to pass `focus` by binding the callable signature before invoking it. Contexts with no focus parameter keep their supported no-argument call; contexts accepting `focus` or `**kwargs` receive the requested focus. A native callable without an inspectable signature receives focus once.

An exception after invocation reports failure without retrying. In particular, an internal `TypeError` may occur after the context has already changed, so it must not be interpreted as permission to run compaction a second time. The wrapper does not claim to undo a context provider's partial mutation. Transactional failure recovery remains the mounted context's responsibility and needs provider-specific acceptance evidence.

`tests/test_compaction_failure_ownership.py` proves body TypeError is called once, no-focus compatibility is retained, kwargs receive focus, and opaque-callable failures are not retried.
