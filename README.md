# 🐍🚀 p2r: Python to Rust Transpiler

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Rust Version](https://img.shields.io/badge/rust-1.50+-orange.svg)](https://www.rust-lang.org/learn/get-started)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

`p2r` is a static Python to Rust transpiler designed to convert a subset of Python code into idiomatic and performant Rust. It's an experimental project aiming to bridge the gap between Python's ease of use and Rust's safety and speed, by providing a direct translation path for certain patterns.

## ✨ Features

- **Two-Pass Compilation**: Handles declarations first, then statement bodies, for robust type inference.
- **Strong Type Unification**: Ensures type compatibility across branches and assignments, mimicking Rust's strictness.
- **Python `range()` to Rust Ranges**: Translates `range(start, end)` directly to `start..end`.
- **Elimination of `UNKNOWN` Types**: Guarantees a fully typed Rust output.
- **Comprehensive Statement Lowering**: Supports variable declarations, assignments, `if`/`else`, `while`, `for` loops, and `return` statements.
- **Method Signature Tracking**: Correctly handles methods within `struct`s (Python `class`es).
- **f-string Completion**: Converts Python f-strings to Rust's `format!` macro.
- **Basic Ownership Handling**: Attempts to infer when `clone()` might be needed for non-primitive types passed to functions.
- **Struct & Method Generation**: Translates Python classes into Rust structs with `impl` blocks for methods.
- **Integrated Build & Run**: Optionally compiles and executes the generated Rust code right after transpilation.
- **Pylance-Clean Codebase**: The transpiler's own Python code is now free of reported Pylance type-hinting issues.
- **Flexible File Handling**: Retains the generated `.rs` file by default and places all output (including executables and debug symbols) in the source directory.

## ⚠️ Current Limitations

While `p2r.py` aims to be comprehensive, it's a work in progress. Here are some key limitations:

- **Subset of Python**: Not all Python features are supported. Focus is on statically typed, imperative code.
  - Dynamic features (e.g., `eval`, arbitrary reflection) are not supported.
  - Advanced Python data structures beyond `list` and `dict` (e.g., `set`, `tuple` as type hints are okay, but direct tuple literals are limited).
  - Complex object-oriented features beyond basic classes with fields and methods.
- **Type Annotations Required**: Python input *must* be fully type-annotated for successful transpilation.
- **Error Handling (Rust `Result`)**: Currently, string parsing methods (`int()`, `float()`) use `.unwrap()`, which can panic on invalid input. Robust error handling using Rust's `Result` type is not yet implemented.
- **Borrow Checker Complexity**: Automatic inference for all borrow checker scenarios is extremely challenging. The transpiler makes conservative choices (like `&mut self` for methods, and `clone()` for non-primitives passed to functions), which might not always be the most optimal or compile-time efficient Rust code.
- **No Imports/Modules**: Does not yet handle Python `import` statements or module transpilation. All code is assumed to be in a single file.
- **Limited Standard Library Mapping**: Only a few built-in functions (`print`, `len`, `str`, `int`, `float`, `range`) and basic list/dict methods are supported.

## 📦 Dependencies

To use `p2r.py`, you'll need:

- **Python 3.8+**: The language runtime for the transpiler itself.
- **Rust Toolchain**: `rustc` and `cargo` for compiling the generated Rust code.
  - Install via `rustup`: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`

## 🛠️ Usage

### 1. Installation (No formal install yet)

Simply clone this repository:

```bash
git clone https://github.com/pro-grammer-SD/p2r.git
cd p2r
```

### 2. Prepare your Python Code

Your Python code *must* include type annotations.

**`example.py`** (or use the demo in the `demo/` folder, it's also precompiled to .exe):

```python
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
```

### 3. Transpile and Compile

Run the `p2r.py` script from your terminal:

```bash
python3 p2r.py example.py
```

...Or if you still want to recompile the test yourself (Windows-only!):

```bash
.\test
```

This command will:

1. Generate `example.rs` in the same directory as `example.py`.
2. Compile `example.rs` into an executable (e.g., `example` or `example.exe`) in the same directory.

#### Command-line Arguments

- `input`: The path to your Python source file (e.g., `my_script.py`).
- `--output`, `-o`: (Optional) Specify the name/path for the generated Rust file. Defaults to `<input_file_name>.rs`.

    ```bash
    python3 p2r.py my_script.py -o custom_name.rs
    ```

- `--no-compile`: (Optional) Only generate the `.rs` file, do not compile it into a binary.

    ```bash
    python3 p2r.py my_script.py --no-compile
    ```

- `--run`: (Optional) Execute the compiled Rust binary immediately after successful compilation.

    ```bash
    python3 p2r.py my_script.py --run
    ```

- `--keep-pdb`: (Optional, Windows only) Keep the `.pdb` debug symbols file after compilation. By default, it's deleted.

    ```bash
    python3 p2r.py my_script.py --keep-pdb
    ```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
