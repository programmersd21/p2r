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

fn add(a: i64, b: i64) -> i64 {
    return a + b;
}

fn main() {
    let mut x: i64 = 42;
    println!("{}", format!("x = {}", x));
    let mut y: i64 = x + 10;
    println!("{}", y);
    let mut result: i64 = add(5, 3);
    println!("{}", result);
    let mut nums: Vec<i64> = vec![1, 2, 3];
    for n in &nums {
        println!("{}", n);
    }
    for i in 0..3 {
        println!("{}", i);
    }
    let mut msg: String = String::from("Hello");
    println!("{}", msg);
    let mut flag: bool = true;
    if flag {
        println!("{}", String::from("Flag is true"));
    }
}
