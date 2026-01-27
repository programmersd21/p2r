x: int = 42
print(f"x = {x}")

y: int = x + 10
print(y)


# Test function
def add(a: int, b: int) -> int:
    return a + b


result: int = add(5, 3)
print(result)

# Test list
nums: list[int] = [1, 2, 3]
for n in nums:
    print(n)

# Test range
for i in range(3):
    print(i)

# Test string
msg: str = "Hello"
print(msg)

# Test bool
flag: bool = True
if flag:
    print("Flag is true")
