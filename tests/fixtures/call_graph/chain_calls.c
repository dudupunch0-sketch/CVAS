// Test: Chain of function calls
// Expected: Call graph depth 2, critical path length 3
CVAS_START
int add(int a, int b) { return a + b; }
int mul(int a, int b) { return a * b; }
int compute(int x, int y) {
    int sum = add(x, y);
    return mul(sum, 2);
}
CVAS_END
