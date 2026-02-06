// Regression fixture for function definition/call parsing
// Intended to exercise multiline params, function pointers, attributes, and inline specs.

CVAS_START

static inline int sum_inline(
    int a,
    int b
) {
    return a + b;
}

__attribute__((unused))
int multiline_params(
    const char *name,
    int (*handler)(int code, const char *msg)
) {
    return handler(0, name);
}

int (*factory(int seed))(int) {
    if (seed > 0) {
        return &sum_inline;
    }
    return 0;
}

int uses_calls(int input) {
    int value = sum_inline(input, 2);
    int result = multiline_params("ok", sum_inline);
    return value + result;
}

CVAS_END
