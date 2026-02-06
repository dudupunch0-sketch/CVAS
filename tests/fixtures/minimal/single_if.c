// Test: Simple if statement
// Expected: 1 compare operation, CFG with conditional branch
CVAS_START
int abs_value(int x) {
    if (x < 0) {
        return -x;
    }
    return x;
}
CVAS_END
