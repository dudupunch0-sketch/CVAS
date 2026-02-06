#define CHECK(x) ((x) > 0)

typedef int (*cb_t)(int, int);

__attribute__((unused)) static int add(int a,
                                       int b) {
    return a + b;
}

int (*make_cb(cb_t cb))(int, int) {
    return cb;
}

int compute(int x, int y) {
    int result = 0;
    for (int i = 0;
         i < x && CHECK(y);
         i++) {
        result += add(i, y);
    }
    if ((x > 0 &&
         y > 0) || CHECK(result))
        result = add(result, x);
    while (result < 100 &&
           CHECK(x)) {
        result = add(result, 1);
    }
    return result;
}
