/* A few original functions for the Xbox 360 demo: no game data, no third-party code.
   Compiled with the user's xsdk GCC under projects/demo-xbox360/build.py. */

int add_i32(int a, int b)
{
    return a + b;
}

int abs_i32(int v)
{
    return v < 0 ? -v : v;
}

int clamp_i32(int v, int lo, int hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

int sum_i32(const int *values, int count)
{
    int total = 0;
    for (int i = 0; i < count; ++i)
        total += values[i];
    return total;
}

int main(void)
{
    int values[4] = {1, -2, 3, -4};
    int total = sum_i32(values, 4);
    return abs_i32(total) == 2 && clamp_i32(total, -1, 1) == -1 && add_i32(total, 2) == 0 ? 0 : 1;
}
