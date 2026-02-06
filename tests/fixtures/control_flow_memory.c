// Fixture: control flow + memory access + macro call
// CVAS_START/END delimiters define the analyzed region.

#include <stddef.h>

#define MACRO_CALL(x) helper((x))

typedef struct {
    int value;
} Node;

// This helper is inside CVAS_START/END and should appear in call_graph.
CVAS_START

static int helper(int input) {
    return input + 1;
}

int analyze(int *ptr, int arr[4], Node *node, int count) {
    int sum = 0;

    if (count > 0) {
        sum = *ptr;
    } else {
        sum = arr[0];
    }

    for (int i = 0; i < count; i++) {
        sum = sum + arr[i];
    }

    int j = 0;
    while (j < count) {
        sum = sum + node->value;
        j = j + 1;
    }

    sum = MACRO_CALL(sum);
    return sum;
}

CVAS_END

// Outside CVAS markers (ignored)
int ignored_outside(int value) {
    return value * 2;
}
