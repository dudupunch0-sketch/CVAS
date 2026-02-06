// Test: do-while loop
// Expected: Loop in CFG
CVAS_START
int count_down(int n) {
    int sum = 0;
    do {
        sum += n;
        n--;
    } while (n > 0);
    return sum;
}
CVAS_END
