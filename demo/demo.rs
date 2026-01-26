#[allow(unused_imports)]
#[allow(unused_variables)]
#[allow(dead_code)]
use std::collections::{HashMap, HashSet};
use std::cmp::{min, max};

fn add(a: i64, b: i64) -> i64 {
    return a + b;
}

fn main() {
    let mut x: i64 = 42;
    println!("x = {}", x);
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
    println!("{}", msg.clone());
    let mut flag: bool = true;
    if flag {
        println!("Flag is true");
    }
}
