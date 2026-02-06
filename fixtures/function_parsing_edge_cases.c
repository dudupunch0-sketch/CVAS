// Regression fixture for function definition/call parsing
// Intended to exercise multiline params, function pointers, attributes, and inline specs.

CVAS_START

typedef struct {
    int field;
} SampleNode;

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

int compound_operator_edges(SampleNode *ptr, int *arr, int *value_ptr, int index) {
    int total = 0;
    arr[index] += 1;
    ptr->field += 3;
    arr[0] *= 2;
    *value_ptr += 4;
    total += arr[index];
    return total;
}

CVAS_END
