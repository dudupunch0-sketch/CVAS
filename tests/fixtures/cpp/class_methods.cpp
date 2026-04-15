class Accum {
public:
    int add1(int x);
    int top(int x);
};

CVAS_START
int Accum::add1(int x) {
    return x + 1;
}

int Accum::top(int x) {
    int y = add1(x);
    return y;
}
CVAS_END
