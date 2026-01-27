# 🐍🚀 p2r: Python to Rust Transpiler

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg?style=for-the-badge)](https://www.python.org/downloads/)
[![Rust Version](https://img.shields.io/badge/rust-1.50+-orange.svg?style=for-the-badge)](https://www.rust-lang.org/learn/get-started)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge)](LICENSE)

`p2r` is a static Python to Rust transpiler that converts statically typed Python code into idiomatic Rust. It bridges Python's simplicity with Rust's speed and safety, currently supporting imperative code, basic classes, and fundamental built-ins.

## ✨ Features

* Two-pass compilation for declarations and statements.
* Strong type unification with Rust-style type safety.
* Automatic translation of Python `range()` to Rust ranges.
* Full elimination of `UNKNOWN` types in output.
* Statement support: variables, assignments, `if`/`else`, `while`, `for`, `return`.
* Methods and struct generation from Python classes.
* f-string to `format!` conversion.
* Basic ownership inference and `clone()` handling.
* Integrated build and optional execution of Rust output.
* Pylance-clean Python codebase.
* Flexible file handling: retains `.rs` and places executables/debug symbols alongside source.

## ⚠️ Limitations

* Subset of Python: dynamic features, advanced data structures beyond lists/dicts, and complex OOP are unsupported.
* Type annotations required.
* Rust error handling (`Result`) not fully implemented; uses `.unwrap()`.
* Borrow checker is only conservatively handled.
* No module or import handling.
* Limited standard library mapping (`print`, `len`, `str`, `int`, `float`, `range`).

## 📦 Dependencies

* Python 3.8+
* Rust toolchain (`rustc` and `cargo`)

## 🛠️ Usage

### 1. Clone Repository

```bash
git clone https://github.com/pro-grammer-SD/p2r.git
cd p2r
```

### 2. Prepare Python Code

Python files must include type annotations. Example:

```python
x: int = 42
print(f"x = {x}")

y: int = x + 10
print(y)

def add(a: int, b: int) -> int:
    return a + b

result: int = add(5, 3)
print(result)

nums: list[int] = [1, 2, 3]
for n in nums:
    print(n)

for i in range(3):
    print(i)

msg: str = "Hello"
print(msg)

flag: bool = True
if flag:
    print("Flag is true")
```

### 3. Transpile and Compile

```bash
python3 p2r.py example.py
```

Generates `example.rs` and compiles to `example`/`example.exe`.

#### Optional Arguments

* `--output` / `-o`: Specify Rust file name.
* `--no-compile`: Only generate `.rs`.
* `--run`: Execute Rust binary after compilation.
* `--keep-pdb`: Keep Windows `.pdb` debug file.

## 📄 License

MIT License — see [LICENSE](LICENSE).
