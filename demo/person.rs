#[allow(unused_imports)]
use std::collections::HashMap;
use std::io::{self, Write};

fn __read_input(prompt: &str) -> String {
    print!("{}", prompt);
    io::stdout().flush().ok();
    let mut input = String::new();
    io::stdin().read_line(&mut input).ok();
    input.trim().to_string()
}

#[derive(Debug, Clone)]
struct Person {
    pub name: String,
    pub age: i64,
}

impl Person {
    pub fn new(name: String, age: i64) -> Self {
        Self {
            name: name,
            age: age,
        }
    }
    pub fn greet(&mut self) {
        println!("{}", format!("Hello, I am {}", self.name));
    }
}

fn main() {
    let mut name: String = __read_input("Enter name: ");
    let mut age: i64 = __read_input("Enter age: ").parse::<i64>().unwrap_or(0);
    let mut p: Person = Person { name: name, age: age };
    p.greet();
    println!("{}", p.age);
}
