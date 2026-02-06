// Test: Simple for loop
// Expected: CFG with loop_header and loop_body blocks
CVAS_START
int sum_n(int n) {
    int sum = 0;
    for (int i = 0; i < n; i++) {
        sum += i;
    }
    return sum;
}
CVAS_END
