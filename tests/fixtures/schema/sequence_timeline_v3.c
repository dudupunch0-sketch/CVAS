// Test: repeated call instances for JSON Schema v3 sequence timeline
CVAS_START
int inc(int x) {
    return x + 1;
}

int top(int a) {
    int first = inc(a);
    int second = inc(first);
    return second;
}
CVAS_END
