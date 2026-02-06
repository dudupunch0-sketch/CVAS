// Fixture: core_cases
// CVAS_START/END included for this snippet.

#include <stddef.h>

CVAS_START
#define CALL_HELPER(x) helper((x))

typedef struct {
    int value;
    int *ptr;
} Node;

static int helper(int v) {
    return v + 1;
}

int analyze(int *arr, int len, Node *node) {
    int sum = 0;
    int a = 1;
    int b = 2;
    int c = 4;
    int cond = a && b;
    int x = cond ? a : b;
    int mask = a & b | c;
    if ((a && b) || c) {
        sum += 1;
    }
    if (len > 0) {
        sum = arr[0];
    } else {
        sum = 1;
    }

    for (int i = 0; i < len; i++) {
        sum += arr[i];
    }

    int j = 0;
    while (j < len) {
        sum += *(arr + j);
        j++;
    }

    if (node && node->ptr) {
        sum += *(node->ptr);
    }

    sum += node->value;
    sum += CALL_HELPER(sum);
    return sum;
}
CVAS_END
