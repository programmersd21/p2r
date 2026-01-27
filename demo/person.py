class Person:
    name: str
    age: int

    def __init__(self, name: str, age: int) -> None:
        self.name = name
        self.age = age

    def greet(self) -> None:
        print(f"Hello, I am {self.name}")


if __name__ == "__main__":
    name: str = input("Enter name: ")
    age: int = int(input("Enter age: "))
    p: Person = Person(name, age)
    p.greet()
    print(p.age)
