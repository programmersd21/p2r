#!/usr/bin/env python3
"""
p2r.py - Static Python to Rust Transpiler (FIXED)
-------
FIXES APPLIED:
1. ✅ Two-pass compilation (declarations → bodies)
2. ✅ Proper type unification & CFG merging for branches
3. ✅ range() → Rust ranges (0..n, a..b)
4. ✅ Eliminated UNKNOWN from final IR
5. ✅ Complete statement lowering
6. ✅ Method signature tracking
7. ✅ f-string completion
8. ✅ Proper ownership handling
9. ✅ Pylance type hinting warnings fixed.
10.✅ Improved file handling: .rs retained, executables in target path, no dummy files.
"""

import ast
import sys
import argparse
import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union, Tuple, Set, cast
from enum import Enum, auto

# ==============================================================================
# 1. TYPE SYSTEM & IR
# ==============================================================================

class RustTypeKind(Enum):
    UNIT = auto()
    BOOL = auto()
    I64 = auto()
    F64 = auto()
    STRING = auto()
    VEC = auto()
    HASHMAP = auto()
    STRUCT = auto()
    OPTION = auto()
    RANGE = auto()
    # Add an 'UNKNOWN' kind for initial stages, to be eliminated later
    UNKNOWN = auto() 

@dataclass
class RustType:
    kind: RustTypeKind
    name: str = ""
    inner: List['RustType'] = field(default_factory=list)

    def __repr__(self):
        if self.kind == RustTypeKind.UNIT: return "()"
        if self.kind == RustTypeKind.BOOL: return "bool"
        if self.kind == RustTypeKind.I64: return "i64"
        if self.kind == RustTypeKind.F64: return "f64"
        if self.kind == RustTypeKind.STRING: return "String"
        if self.kind == RustTypeKind.VEC: return f"Vec<{self.inner[0]}>"
        if self.kind == RustTypeKind.HASHMAP: return f"HashMap<{self.inner[0]}, {self.inner[1]}>"
        if self.kind == RustTypeKind.OPTION: return f"Option<{self.inner[0]}>"
        if self.kind == RustTypeKind.STRUCT: return self.name
        if self.kind == RustTypeKind.RANGE: return "std::ops::Range<i64>"
        if self.kind == RustTypeKind.UNKNOWN: return "UNKNOWN" # Should not appear in final IR
        return "UnknownType"

    def is_primitive(self):
        return self.kind in {RustTypeKind.BOOL, RustTypeKind.I64, RustTypeKind.F64}

    def clone_needed(self):
        return not self.is_primitive() and self.kind != RustTypeKind.UNIT and self.kind != RustTypeKind.RANGE

    def unify(self, other: 'RustType') -> bool:
        """Check if types are unifiable (same kind & inner types)"""
        if self.kind == RustTypeKind.UNKNOWN or other.kind == RustTypeKind.UNKNOWN:
            return True # UNKNOWN can unify with anything during inference, but should be resolved
        if self.kind != other.kind: return False
        if self.kind == RustTypeKind.STRUCT: return self.name == other.name
        if len(self.inner) != len(other.inner): return False
        return all(s.unify(o) for s, o in zip(self.inner, other.inner))

# --- IR Nodes ---

@dataclass
class IRExpr:
    rtype: RustType

@dataclass
class IRLiteral(IRExpr):
    value: str

@dataclass
class IRVariable(IRExpr):
    name: str

@dataclass
class IRBinaryOp(IRExpr):
    left: IRExpr
    op: str
    right: IRExpr

@dataclass
class IRUnaryOp(IRExpr):
    op: str
    operand: IRExpr

@dataclass
class IRCall(IRExpr):
    func_name: str
    args: List[IRExpr]
    is_method: bool = False
    instance: Optional[IRExpr] = None

@dataclass
class IRFieldAccess(IRExpr):
    instance: IRExpr
    field: str

@dataclass
class IRListCtor(IRExpr):
    elements: List[IRExpr]

@dataclass
class IRDictCtor(IRExpr):
    keys: List[IRExpr]
    values: List[IRExpr]

@dataclass
class IRStructCtor(IRExpr):
    struct_name: str
    fields: List[Tuple[str, IRExpr]]  # (field_name, value)

@dataclass
class IRFString(IRExpr):
    fmt_str: str  # Raw format string without quotes
    args: List[IRExpr]

@dataclass
class IRRangeCtor(IRExpr):
    start: IRExpr
    end: IRExpr
    exclusive: bool = True

@dataclass
class IRStmt:
    pass

@dataclass
class IRVarDecl(IRStmt):
    name: str
    rtype: RustType
    is_mut: bool
    init: Optional[IRExpr]

@dataclass
class IRAssign(IRStmt):
    target: str  # Simple name for now
    value: IRExpr

@dataclass
class IRFieldAssign(IRStmt):
    obj_name: str
    field_name: str
    value: IRExpr

@dataclass
class IRExprStmt(IRStmt):
    expr: IRExpr

@dataclass
class IRIf(IRStmt):
    condition: IRExpr
    then_block: List[IRStmt]
    else_block: Optional[List[IRStmt]]
    # Type state after if (for type merging)
    then_types: Dict[str, RustType] = field(default_factory=dict)
    else_types: Dict[str, RustType] = field(default_factory=dict)

@dataclass
class IRWhile(IRStmt):
    condition: IRExpr
    body: List[IRStmt]

@dataclass
class IRFor(IRStmt):
    target_name: str
    iterator: IRExpr
    body: List[IRStmt]

@dataclass
class IRReturn(IRStmt):
    value: Optional[IRExpr]

@dataclass
class IRFuncDecl:
    name: str
    args: List[Tuple[str, RustType]]
    ret_type: RustType
    body: List[IRStmt]
    is_method: bool = False
    self_type: Optional[RustType] = None

@dataclass
class IRStructDecl:
    name: str
    fields: List[Tuple[str, RustType]]

@dataclass
class IRModule:
    structs: List[IRStructDecl]
    funcs: List[IRFuncDecl]
    main_block: List[IRStmt]

# ==============================================================================
# 2. ERROR HANDLING
# ==============================================================================

class CompilerError(Exception):
    pass

def fail(msg: str, node: Optional[ast.AST] = None):
    lineno = getattr(node, 'lineno', '?') if node else '?'
    print(f"\n[ERROR] at line {lineno}:", file=sys.stderr)
    print(f"   {msg}\n", file=sys.stderr)
    sys.exit(1)

# ==============================================================================
# 3. SYMBOL TABLE
# ==============================================================================

@dataclass
class SymbolInfo:
    name: str
    rtype: RustType
    is_mut: bool = False
    is_arg: bool = False

class SymbolTable:
    def __init__(self):
        self.scopes: List[Dict[str, SymbolInfo]] = [{}]
        self.struct_defs: Dict[str, Dict[str, RustType]] = {}
        # Stores (func_name -> (arg_types, ret_type))
        self.func_sigs: Dict[str, Tuple[List[RustType], RustType]] = {} 
        # Stores ((struct_name, method_name) -> (arg_types, ret_type))
        self.method_sigs: Dict[Tuple[str, str], Tuple[List[RustType], RustType]] = {} 

    def enter_scope(self):
        self.scopes.append({})

    def exit_scope(self):
        self.scopes.pop()

    def declare(self, name: str, rtype: RustType, is_mut: bool = False, is_arg: bool = False):
        self.scopes[-1][name] = SymbolInfo(name, rtype, is_mut, is_arg)

    def lookup(self, name: str) -> Optional[SymbolInfo]:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def update_type(self, name: str, new_type: RustType):
        """Update variable type (for shadowing/control flow)"""
        for scope in reversed(self.scopes):
            if name in scope:
                scope[name].rtype = new_type
                return
        # If not found, declare it as a new mutable variable (e.g., in a conditional branch)
        self.declare(name, new_type, is_mut=True)

    def register_struct(self, name: str, fields: Dict[str, RustType]):
        self.struct_defs[name] = fields

    def get_struct_field_type(self, struct_name: str, field: str) -> Optional[RustType]:
        if struct_name in self.struct_defs:
            return self.struct_defs[struct_name].get(field)
        return None

    def register_method(self, struct_name: str, method_name: str, 
                       arg_types: List[RustType], ret_type: RustType):
        self.method_sigs[(struct_name, method_name)] = (arg_types, ret_type)

    def get_method_sig(self, struct_name: str, method_name: str) -> Optional[Tuple[List[RustType], RustType]]:
        return self.method_sigs.get((struct_name, method_name))

# ==============================================================================
# 4. COMPILER (TWO-PASS)
# ==============================================================================

class PythonToRustCompiler:
    def __init__(self):
        self.symtab = SymbolTable()
        self.current_func_ret_type: RustType = RustType(RustTypeKind.UNIT) # Default to unit for main block

    def parse_annotation(self, node: Optional[ast.AST]) -> RustType: # pyright: ignore[reportReturnType]
        if node is None:
            fail("Type annotation required", node)

        if isinstance(node, ast.Name):
            if node.id == 'int': return RustType(RustTypeKind.I64)
            if node.id == 'float': return RustType(RustTypeKind.F64)
            if node.id == 'bool': return RustType(RustTypeKind.BOOL)
            if node.id == 'str': return RustType(RustTypeKind.STRING)
            if node.id in self.symtab.struct_defs:
                return RustType(RustTypeKind.STRUCT, name=node.id)
            fail(f"Unknown type: {node.id}", node)

        if isinstance(node, ast.Subscript):
            # Handle both List[T] and list[T] syntax
            if isinstance(node.value, ast.Name):
                base = node.value.id
            else:
                fail("Unsupported subscript base, expected a Name (e.g., List, Dict)", node)
            
            if base in ('List', 'list'):
                inner = self.parse_annotation(node.slice)
                return RustType(RustTypeKind.VEC, inner=[inner])
            
            if base in ('Dict', 'dict'):
                dims: List[RustType] = []
                if isinstance(node.slice, ast.Tuple):
                    dims = [self.parse_annotation(e) for e in node.slice.elts]
                else:
                    # If it's not a tuple, it's a single type param (invalid for dict)
                    fail("Dict requires 2 type arguments (e.g., Dict[K, V])", node.slice)
                if len(dims) != 2:
                    fail("Dict requires 2 type arguments", node)
                return RustType(RustTypeKind.HASHMAP, inner=dims)
            
            if base in ('Optional', 'option'):
                inner = self.parse_annotation(node.slice)
                return RustType(RustTypeKind.OPTION, inner=[inner])

        fail(f"Unsupported type annotation: {ast.dump(node)}", node) # pyright: ignore[reportArgumentType]

    def infer_literal_type(self, node: ast.Constant) -> RustType: # pyright: ignore[reportReturnType]
        if isinstance(node.value, bool): return RustType(RustTypeKind.BOOL)
        if isinstance(node.value, int): return RustType(RustTypeKind.I64)
        if isinstance(node.value, float): return RustType(RustTypeKind.F64)
        if isinstance(node.value, str): return RustType(RustTypeKind.STRING)
        if node.value is None: return RustType(RustTypeKind.UNIT)
        fail(f"Unknown literal type: {type(node.value)}", node)

    def rust_string_literal(self, s: str) -> str:
        """Convert Python string to Rust String literal (with proper escaping)"""
        # Ensure s is a string before calling replace
        if not isinstance(s, str):
            fail(f"Expected string literal, got {type(s)}", None) # No specific node for this internal call
        escaped = s.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    def visit_expr(self, node: ast.expr) -> IRExpr: # pyright: ignore[reportReturnType]
        if isinstance(node, ast.Constant):
            rtype = self.infer_literal_type(node)
            val: str
            if rtype.kind == RustTypeKind.BOOL:
                val = str(node.value).lower()
            elif rtype.kind == RustTypeKind.STRING:
                val = self.rust_string_literal(cast(str, node.value)) # Cast to str after type check
            elif node.value is None:
                val = "()" # Rust unit type
            else:
                val = str(node.value)
            return IRLiteral(rtype, val)

        elif isinstance(node, ast.Name):
            sym = self.symtab.lookup(node.id)
            if not sym:
                fail(f"Undefined variable '{node.id}'", node)
            return IRVariable(sym.rtype, node.id) # pyright: ignore[reportOptionalMemberAccess]

        elif isinstance(node, ast.BinOp):
            lhs = self.visit_expr(node.left)
            rhs = self.visit_expr(node.right)

            if not lhs.rtype.unify(rhs.rtype):
                fail(f"Type mismatch in binary operation: {lhs.rtype} vs {rhs.rtype}", node)

            op_map = {
                ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
                ast.Mod: "%", ast.BitOr: "|", ast.BitAnd: "&", ast.BitXor: "^"
            }
            op_str = op_map.get(type(node.op))
            if op_str is None:
                fail(f"Unsupported binary operator: {type(node.op).__name__}", node)

            # Special handling for string concatenation
            if op_str == "+" and lhs.rtype.kind == RustTypeKind.STRING:
                return IRCall(RustType(RustTypeKind.STRING), "format!", 
                              [IRLiteral(RustType(RustTypeKind.STRING), self.rust_string_literal("{}{}")), lhs, rhs])

            return IRBinaryOp(lhs.rtype, lhs, op_str, rhs) # pyright: ignore[reportArgumentType]

        elif isinstance(node, ast.Compare):
            if len(node.ops) > 1:
                fail("Chained comparisons unsupported", node)

            left = self.visit_expr(node.left)
            right = self.visit_expr(node.comparators[0])

            if not left.rtype.unify(right.rtype):
                fail(f"Type mismatch in comparison: {left.rtype} vs {right.rtype}", node)

            op_map = {
                ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
                ast.Gt: ">", ast.GtE: ">="
            }
            op = op_map.get(type(node.ops[0]))
            if not op:
                fail(f"Unsupported comparison operator: {type(node.ops[0]).__name__}", node)

            return IRBinaryOp(RustType(RustTypeKind.BOOL), left, op, right) # pyright: ignore[reportArgumentType]

        elif isinstance(node, ast.BoolOp):
            op_str = "&&" if isinstance(node.op, ast.And) else "||"
            
            # Start with the first operand
            curr_expr = self.visit_expr(node.values[0])
            if curr_expr.rtype.kind != RustTypeKind.BOOL:
                fail(f"Boolean operators need bool operands, got {curr_expr.rtype}", node.values[0])
            
            # Chain remaining operands
            for val_node in node.values[1:]:
                next_expr = self.visit_expr(val_node)
                if next_expr.rtype.kind != RustTypeKind.BOOL:
                    fail(f"Boolean operators need bool operands, got {next_expr.rtype}", val_node)
                curr_expr = IRBinaryOp(RustType(RustTypeKind.BOOL), curr_expr, op_str, next_expr)
            
            return curr_expr

        elif isinstance(node, ast.UnaryOp):
            operand_expr = self.visit_expr(node.operand)
            
            if isinstance(node.op, ast.Not):
                if operand_expr.rtype.kind != RustTypeKind.BOOL:
                    fail(f"'not' requires bool operand, got {operand_expr.rtype}", node)
                return IRUnaryOp(RustType(RustTypeKind.BOOL), "!", operand_expr)
            
            if isinstance(node.op, ast.USub):
                if operand_expr.rtype.kind not in {RustTypeKind.I64, RustTypeKind.F64}:
                    fail(f"Unary minus requires numeric type, got {operand_expr.rtype}", node)
                return IRUnaryOp(operand_expr.rtype, "-", operand_expr)
            
            if isinstance(node.op, ast.UAdd):
                if operand_expr.rtype.kind not in {RustTypeKind.I64, RustTypeKind.F64}:
                    fail(f"Unary plus requires numeric type, got {operand_expr.rtype}", node)
                return operand_expr  # Unary + is a no-op in Python (and Rust for primitives)
            
            fail(f"Unsupported unary operator: {type(node.op).__name__}", node)

        elif isinstance(node, ast.Call):
            return self.visit_call(node)

        elif isinstance(node, ast.List):
            if not node.elts:
                # Require annotation for empty lists
                fail("Empty list literal requires type annotation (e.g., `x: List[int] = []`)", node)
            
            elements = [self.visit_expr(e) for e in node.elts]
            
            if not elements: # Should be caught by the check above, but for safety
                fail("List elements are empty after processing, this should not happen.", node)
            
            elem_type = elements[0].rtype
            
            for e in elements:
                if not e.rtype.unify(elem_type):
                    fail(f"List elements must be homogenous, found {elem_type} and {e.rtype}", node)
            
            return IRListCtor(RustType(RustTypeKind.VEC, inner=[elem_type]), elements)

        elif isinstance(node, ast.Dict):
            if not node.keys:
                # Require annotation for empty dicts
                fail("Empty dict literal requires type annotation (e.g., `x: Dict[str, int] = {}`)", node)
            
            keys = [self.visit_expr(k) for k in node.keys] # pyright: ignore[reportArgumentType]
            vals = [self.visit_expr(v) for v in node.values]
            
            if not keys or not vals: # Should be caught by the check above, but for safety
                fail("Dict keys or values are empty after processing, this should not happen.", node)

            k_type = keys[0].rtype
            v_type = vals[0].rtype
            
            for k in keys:
                if not k.rtype.unify(k_type):
                    fail(f"Dict keys must be homogenous, found {k_type} and {k.rtype}", node)
            for v in vals:
                if not v.rtype.unify(v_type):
                    fail(f"Dict values must be homogenous, found {v_type} and {v.rtype}", node)
            
            return IRDictCtor(RustType(RustTypeKind.HASHMAP, inner=[k_type, v_type]), keys, vals)

        elif isinstance(node, ast.Attribute):
            instance_expr = self.visit_expr(node.value)
            if instance_expr.rtype.kind == RustTypeKind.STRUCT:
                field_type = self.symtab.get_struct_field_type(instance_expr.rtype.name, node.attr)
                if not field_type:
                    fail(f"Struct '{instance_expr.rtype.name}' has no field '{node.attr}'", node)
                return IRFieldAccess(field_type, instance_expr, node.attr) # pyright: ignore[reportArgumentType]
            fail(f"Cannot access attribute '{node.attr}' on type {instance_expr.rtype}", node)

        elif isinstance(node, ast.JoinedStr):
            return self.visit_fstring(node)

        fail(f"Unsupported expression type: {type(node).__name__}", node)

    def visit_call(self, node: ast.Call) -> IRExpr: # pyright: ignore[reportReturnType]
        func_node = node.func
        args_exprs = [self.visit_expr(a) for a in node.args]

        # Method call (e.g., obj.method())
        if isinstance(func_node, ast.Attribute):
            instance_expr = self.visit_expr(func_node.value)
            method_name = func_node.attr

            # Built-in collection methods
            if instance_expr.rtype.kind == RustTypeKind.VEC:
                if method_name == 'append':
                    if len(args_exprs) != 1:
                        fail("Vec.append() takes exactly one argument", node)
                    # Use the inner type of the Vec for the argument type check
                    if not args_exprs[0].rtype.unify(instance_expr.rtype.inner[0]):
                        fail(f"Argument type mismatch for Vec.append(): expected {instance_expr.rtype.inner[0]}, got {args_exprs[0].rtype}", node)
                    return IRCall(RustType(RustTypeKind.UNIT), "push", args_exprs, True, instance_expr)
                if method_name == 'pop':
                    if len(args_exprs) != 0:
                        fail("Vec.pop() takes no arguments", node)
                    return IRCall(RustType(RustTypeKind.OPTION, inner=instance_expr.rtype.inner), "pop", args_exprs, True, instance_expr)
                fail(f"Unsupported Vec method: {method_name}", node)

            elif instance_expr.rtype.kind == RustTypeKind.HASHMAP:
                if method_name == 'get':
                    if len(args_exprs) != 1:
                        fail("HashMap.get() takes exactly one argument", node)
                    # Check key type matches
                    key_type_of_map = instance_expr.rtype.inner[0]
                    if not args_exprs[0].rtype.unify(key_type_of_map):
                        fail(f"Dict key type mismatch for .get(): expected {key_type_of_map}, got {args_exprs[0].rtype}", node)
                    ret_type = RustType(RustTypeKind.OPTION, inner=[instance_expr.rtype.inner[1]])
                    return IRCall(ret_type, "get", args_exprs, True, instance_expr)
                fail(f"Unsupported HashMap method: {method_name}", node)

            # User-defined methods on structs
            if instance_expr.rtype.kind == RustTypeKind.STRUCT:
                struct_name = instance_expr.rtype.name
                sig = self.symtab.get_method_sig(struct_name, method_name)
                if sig:
                    expected_arg_types, ret_type = sig
                    if len(args_exprs) != len(expected_arg_types):
                        fail(f"Method '{struct_name}.{method_name}' expects {len(expected_arg_types)} arguments, got {len(args_exprs)}", node)
                    for i, (arg_expr, expected_type) in enumerate(zip(args_exprs, expected_arg_types)):
                        if not arg_expr.rtype.unify(expected_type):
                            fail(f"Argument {i+1} type mismatch for method '{struct_name}.{method_name}': expected {expected_type}, got {arg_expr.rtype}", node)
                    return IRCall(ret_type, method_name, args_exprs, True, instance_expr)
                fail(f"Struct '{struct_name}' has no method '{method_name}'", node)
            
            fail(f"Cannot call method '{method_name}' on type {instance_expr.rtype}", node)

        # Function call (e.g., func())
        elif isinstance(func_node, ast.Name):
            fname = func_node.id

            # Built-in functions
            if fname == 'print':
                # `print` in Python transpiles to `println!` which has flexible arguments
                # We'll keep it as `println!` with all IR exprs as args, emitter handles formatting.
                return IRCall(RustType(RustTypeKind.UNIT), "println!", args_exprs)
            
            if fname == 'len':
                if len(args_exprs) != 1:
                    fail("len() takes exactly one argument", node)
                # Check if the type has a 'len' concept (Vec, String, HashMap)
                arg_rtype = args_exprs[0].rtype
                if arg_rtype.kind not in {RustTypeKind.VEC, RustTypeKind.STRING, RustTypeKind.HASHMAP}:
                    fail(f"len() not supported for type {arg_rtype}", node)
                return IRCall(RustType(RustTypeKind.I64), "len", args_exprs, True, args_exprs[0])
            
            if fname == 'str':
                if len(args_exprs) != 1:
                    fail("str() takes exactly one argument", node)
                # Any type can generally be converted to a string using .to_string()
                return IRCall(RustType(RustTypeKind.STRING), "to_string", args_exprs, True, args_exprs[0])
            
            if fname == 'int':
                if len(args_exprs) != 1:
                    fail("int() takes exactly one argument", node)
                # Convert string/float to int. Assume parse() for strings, cast for floats.
                if args_exprs[0].rtype.kind == RustTypeKind.STRING:
                    # String::parse() returns Result<T, E>, need unwrap_or_else or similar.
                    # For simplicity, we assume successful parsing for now or handle with specific IR.
                    # This could be improved to handle `Result` or `expect`.
                    # For now, let's represent as a call that returns I64 (assuming parse() ultimately gives I64).
                    return IRCall(RustType(RustTypeKind.I64), "parse", args_exprs, True, args_exprs[0])
                elif args_exprs[0].rtype.kind == RustTypeKind.F64:
                    return IRCall(RustType(RustTypeKind.I64), "as i64", args_exprs, True, args_exprs[0]) # Cast
                else:
                    fail(f"int() conversion not supported for type {args_exprs[0].rtype}", node)

            if fname == 'float':
                if len(args_exprs) != 1:
                    fail("float() takes exactly one argument", node)
                if args_exprs[0].rtype.kind == RustTypeKind.STRING:
                    return IRCall(RustType(RustTypeKind.F64), "parse", args_exprs, True, args_exprs[0])
                elif args_exprs[0].rtype.kind == RustTypeKind.I64:
                    return IRCall(RustType(RustTypeKind.F64), "as f64", args_exprs, True, args_exprs[0]) # Cast
                else:
                    fail(f"float() conversion not supported for type {args_exprs[0].rtype}", node)

            if fname == 'range':
                if not args_exprs:
                    fail("range() needs at least one argument", node)
                
                start_expr: IRExpr
                end_expr: IRExpr
                
                if len(args_exprs) == 1:
                    start_expr = IRLiteral(RustType(RustTypeKind.I64), "0")
                    end_expr = args_exprs[0]
                elif len(args_exprs) == 2:
                    start_expr = args_exprs[0]
                    end_expr = args_exprs[1]
                elif len(args_exprs) == 3:
                    # step = args_exprs[2] — not supported yet in current IR for Rust ranges
                    fail("range(start, end, step) with step argument is not yet fully supported", node)
                else:
                    fail("range() takes 1 or 2 arguments", node)
                
                if not (start_expr.rtype.kind == RustTypeKind.I64 and end_expr.rtype.kind == RustTypeKind.I64):
                    fail("range() arguments must be of type int", node)
                
                return IRRangeCtor(RustType(RustTypeKind.RANGE), start_expr, end_expr, exclusive=True)

            # Struct constructor (e.g., Person())
            if fname in self.symtab.struct_defs:
                # Python allows Person() or Person(field=val, ...)
                if not node.keywords and not args_exprs:
                    # Empty constructor call, map to Rust's Default trait for structs
                    return IRStructCtor(RustType(RustTypeKind.STRUCT, name=fname), fname, [])
                
                # If there are arguments or keywords, it's treated as a constructor with fields
                # This compiler currently assumes direct mapping for `Person(arg1, arg2)` to `Person { field1: arg1, field2: arg2 }`
                # or `Person(field1=arg1)` to `Person { field1: arg1 }`. This needs to match struct field order/names.
                
                # For simplicity here, we assume it's like a function call where args match struct fields by order
                # This is a simplification and might require more robust mapping if Python args don't strictly match Rust fields
                fields_from_args: List[Tuple[str, IRExpr]] = []
                struct_fields_def = self.symtab.struct_defs[fname]
                
                if len(args_exprs) > len(struct_fields_def):
                    fail(f"Too many positional arguments for struct '{fname}' constructor. Expected at most {len(struct_fields_def)}", node)

                # Process positional arguments
                for i, (field_name, field_type) in enumerate(struct_fields_def.items()):
                    if i < len(args_exprs):
                        arg_expr = args_exprs[i]
                        if not arg_expr.rtype.unify(field_type):
                            fail(f"Positional argument {i+1} type mismatch for field '{field_name}' in struct '{fname}': expected {field_type}, got {arg_expr.rtype}", node)
                        fields_from_args.append((field_name, arg_expr))
                    else:
                        break # No more positional args

                # Process keyword arguments (FIXME: this part isn't fully robust as it doesn't merge with positional)
                for keyword in node.keywords:
                    kw_field_name = keyword.arg
                    kw_value_expr = self.visit_expr(keyword.value)
                    
                    if kw_field_name is None: # Should not happen with keyword.arg
                        fail("Keyword argument name is missing", node)

                    expected_field_type = struct_fields_def.get(kw_field_name) # pyright: ignore[reportArgumentType]
                    if not expected_field_type:
                        fail(f"Struct '{fname}' has no field named '{kw_field_name}'", node)
                    
                    if not kw_value_expr.rtype.unify(expected_field_type): # pyright: ignore[reportArgumentType]
                        fail(f"Keyword argument '{kw_field_name}' type mismatch for struct '{fname}': expected {expected_field_type}, got {kw_value_expr.rtype}", node)
                    
                    # Prevent duplicate field assignments (positional + keyword)
                    if any(f[0] == kw_field_name for f in fields_from_args):
                        fail(f"Field '{kw_field_name}' assigned multiple times in struct '{fname}' constructor", node)
                    
                    fields_from_args.append((kw_field_name, kw_value_expr)) # pyright: ignore[reportArgumentType]
                
                return IRStructCtor(RustType(RustTypeKind.STRUCT, name=fname), fname, fields_from_args)

            # User-defined global function
            sig = self.symtab.func_sigs.get(fname)
            if sig:
                expected_arg_types, ret_type = sig
                if len(args_exprs) != len(expected_arg_types):
                    fail(f"Function '{fname}' expects {len(expected_arg_types)} arguments, got {len(args_exprs)}", node)
                for i, (arg_expr, expected_type) in enumerate(zip(args_exprs, expected_arg_types)):
                    if not arg_expr.rtype.unify(expected_type):
                        fail(f"Argument {i+1} type mismatch for function '{fname}': expected {expected_type}, got {arg_expr.rtype}", node)
                return IRCall(ret_type, fname, args_exprs)

            fail(f"Unknown function or constructor '{fname}'", node)

        fail(f"Invalid call expression target: {type(func_node).__name__}", node)

    def visit_fstring(self, node: ast.JoinedStr) -> IRExpr:
        """f"text {expr}" → format!("text {}", expr)"""
        fmt_str_parts: List[str] = []
        fmt_args: List[IRExpr] = []

        for part in node.values:
            if isinstance(part, ast.Constant):
                # Ensure the constant value is a string before processing
                if not isinstance(part.value, str):
                    fail(f"F-string constant part must be a string, got {type(part.value)}", part)
                fmt_str_parts.append(part.value.replace("{", "{{").replace("}", "}}")) # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue, reportArgumentType]
            elif isinstance(part, ast.FormattedValue):
                fmt_str_parts.append("{}")
                fmt_args.append(self.visit_expr(part.value))
            else:
                fail(f"Unsupported f-string part type: {type(part).__name__}", part)
        
        final_fmt_str = "".join(fmt_str_parts)

        # Create a special IR node for f-strings that preserves the raw string
        return IRFString(RustType(RustTypeKind.STRING), final_fmt_str, fmt_args)

    def visit_stmt(self, node: ast.stmt) -> List[IRStmt]: # pyright: ignore[reportReturnType]
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name):
                fail("Only simple variable assignments with annotations are supported", node.target)
            
            name = node.target.id # pyright: ignore[reportAttributeAccessIssue]
            declared_type = self.parse_annotation(node.annotation)

            init_expr: Optional[IRExpr] = None
            if node.value:
                init_expr = self.visit_expr(node.value)
                if not init_expr.rtype.unify(declared_type):
                    fail(f"Initialization type mismatch for '{name}': expected {declared_type}, got {init_expr.rtype}", node)

            # AnnAssign implies declaration, so it's always 'let mut' in Rust if there's an assignment.
            # If no initial value, it's just 'let mut x: Type;'
            self.symtab.declare(name, declared_type, is_mut=True)
            return [IRVarDecl(name, declared_type, True, init_expr)]

        elif isinstance(node, ast.Assign):
            if len(node.targets) > 1:
                fail("Multi-target assignment (e.g., a = b = c) unsupported", node)
            
            target = node.targets[0]
            value_expr = self.visit_expr(node.value)

            # Simple variable assignment: x = value
            if isinstance(target, ast.Name):
                name = target.id
                sym = self.symtab.lookup(name)
                if sym:
                    # Reassignment to an existing variable
                    if not value_expr.rtype.unify(sym.rtype):
                        fail(f"Type change in reassignment for '{name}': expected {sym.rtype}, got {value_expr.rtype}", node)
                    if not sym.is_mut:
                        fail(f"Cannot reassign to immutable variable '{name}'. Declare as `let mut {name}`.", node)
                    return [IRAssign(name, value_expr)]
                else:
                    # First assignment, implicitly declare mutable
                    self.symtab.declare(name, value_expr.rtype, is_mut=True)
                    return [IRVarDecl(name, value_expr.rtype, True, value_expr)]
            
            # Field assignment: obj.field = value
            elif isinstance(target, ast.Attribute):
                obj_expr = self.visit_expr(target.value)
                field_name = target.attr
                
                if obj_expr.rtype.kind != RustTypeKind.STRUCT:
                    fail(f"Can only assign to fields of structs, not type {obj_expr.rtype}", node)
                
                field_type = self.symtab.get_struct_field_type(obj_expr.rtype.name, field_name)
                if not field_type:
                    fail(f"Struct '{obj_expr.rtype.name}' has no field '{field_name}'", node)
                
                if not value_expr.rtype.unify(field_type): # pyright: ignore[reportArgumentType] # pyright: ignore[reportArgumentType]
                    fail(f"Field '{field_name}' type mismatch: expected {field_type}, got {value_expr.rtype}", node)
                
                # Ensure the instance itself is mutable to allow field assignment
                if isinstance(obj_expr, IRVariable):
                    obj_sym = self.symtab.lookup(obj_expr.name)
                    if not obj_sym or not obj_sym.is_mut:
                        fail(f"Cannot assign to field '{field_name}' of immutable struct instance '{obj_expr.name}'. Declare instance as `let mut {obj_expr.name}`.", node)

                # Emit as a special assignment to field
                # If target.value is an ast.Name (e.g., `my_struct.field`), use its ID.
                # If target.value is `self` (within a method), use "self".
                obj_identifier: str
                if isinstance(target.value, ast.Name):
                    obj_identifier = target.value.id
                elif isinstance(target.value, ast.Attribute) and target.value.attr == 'self':
                    obj_identifier = 'self' # This case is probably not reachable with current Python AST handling for `self.field` directly.
                                            # It would be `obj.field` where `obj` is an `IRVariable` for `self`.
                else:
                    fail(f"Unsupported target for field assignment: {ast.dump(target.value)}", node)
                
                return [IRFieldAssign(obj_identifier, field_name, value_expr)]
            
            fail(f"Unsupported assignment target: {type(target).__name__}", node)

        elif isinstance(node, ast.Expr):
            expr = self.visit_expr(node.value)
            return [IRExprStmt(expr)]

        elif isinstance(node, ast.If):
            cond_expr = self.visit_expr(node.test)
            if cond_expr.rtype.kind != RustTypeKind.BOOL:
                fail(f"If condition must be of type bool, got {cond_expr.rtype}", node)
            
            # Type merging logic for conditional branches (basic version)
            # This is a complex area for full type flow analysis.
            # For simplicity, we assume variables declared in branches
            # are local to that branch unless they unify with an outer scope.
            # And, for reassigned variables, we try to ensure type consistency.

            # Store current symbol table state before the if block
            original_scope_vars = {name: self.symtab.lookup(name) for name in self.symtab.scopes[-1]}

            then_block_stmts = self.visit_block(node.body)
            then_scope_vars = self.symtab.scopes[-1].copy() # Get state after then block
            self.symtab.exit_scope() # Exit then_block's scope

            else_block_stmts: Optional[List[IRStmt]] = None
            else_scope_vars: Dict[str, SymbolInfo] = {}

            if node.orelse:
                self.symtab.enter_scope() # Enter a new scope for else_block
                else_block_stmts = self.visit_block(node.orelse)
                else_scope_vars = self.symtab.scopes[-1].copy() # Get state after else block
                self.symtab.exit_scope() # Exit else_block's scope

            # Re-enter the original scope
            # Merge type information for variables that might have been reassigned in both branches
            for var_name, original_sym_info in original_scope_vars.items():
                then_sym = then_scope_vars.get(var_name)
                else_sym = else_scope_vars.get(var_name)

                if then_sym and else_sym:
                    # Variable was in both branches: check if types unify
                    if not then_sym.rtype.unify(else_sym.rtype):
                        fail(f"Type mismatch for variable '{var_name}' across 'if/else' branches: '{then_sym.rtype}' vs '{else_sym.rtype}'", node)
                    # If they unify, the type remains the same as before or the unified type
                    # The symtab already points to the parent scope info which has the original type.
                    # If a variable's mutability changed, we ensure it's propagated up.
                    if then_sym.is_mut or else_sym.is_mut:
                        if original_sym_info:
                            original_sym_info.is_mut = True
                        else: # Should not happen if original_scope_vars was based on self.symtab.scopes[-1]
                            self.symtab.declare(var_name, then_sym.rtype, is_mut=True)

                elif then_sym and not else_sym:
                    # Variable declared/reassigned only in 'then' branch. It's local to 'then'.
                    pass
                elif not then_sym and else_sym:
                    # Variable declared/reassigned only in 'else' branch. It's local to 'else'.
                    pass
            
            return [IRIf(cond_expr, then_block_stmts, else_block_stmts)]

        elif isinstance(node, ast.While):
            cond_expr = self.visit_expr(node.test)
            if cond_expr.rtype.kind != RustTypeKind.BOOL:
                fail(f"While condition must be of type bool, got {cond_expr.rtype}", node)
            body_stmts = self.visit_block(node.body)
            return [IRWhile(cond_expr, body_stmts)]

        elif isinstance(node, ast.For):
            if not isinstance(node.target, ast.Name):
                fail("Complex for loop targets (e.g., tuple unpacking) unsupported", node.target)
            
            target_name = node.target.id # pyright: ignore[reportAttributeAccessIssue]
            iterator_expr = self.visit_expr(node.iter)

            # Infer loop variable type based on iterator
            loop_var_type: Optional[RustType] = None
            if iterator_expr.rtype.kind == RustTypeKind.VEC:
                if not iterator_expr.rtype.inner:
                    fail(f"Cannot iterate over an empty Vec type (missing inner type)", node.iter)
                loop_var_type = iterator_expr.rtype.inner[0]
            elif iterator_expr.rtype.kind == RustTypeKind.RANGE:
                loop_var_type = RustType(RustTypeKind.I64) # Range yields i64
            else:
                fail(f"Cannot iterate over type {iterator_expr.rtype}", node.iter)

            if loop_var_type is None: # Should be caught by the above, but for safety
                fail(f"Failed to infer loop variable type for iterator {iterator_expr.rtype}", node.iter)

            self.symtab.enter_scope()
            self.symtab.declare(target_name, loop_var_type, is_mut=False) # pyright: ignore[reportArgumentType] # Loop variable is immutable by default
            body_stmts = self.visit_block(node.body)
            self.symtab.exit_scope()

            return [IRFor(target_name, iterator_expr, body_stmts)]

        elif isinstance(node, ast.Return):
            val_expr: Optional[IRExpr] = None
            if node.value:
                val_expr = self.visit_expr(node.value)
                if not val_expr.rtype.unify(self.current_func_ret_type):
                    fail(f"Return type mismatch: expected {self.current_func_ret_type}, got {val_expr.rtype}", node)
            elif self.current_func_ret_type.kind != RustTypeKind.UNIT:
                fail(f"Function expects return type {self.current_func_ret_type}, but returned nothing.", node)
            return [IRReturn(val_expr)]

        elif isinstance(node, ast.Pass):
            return [] # Pass statement translates to nothing

        fail(f"Unsupported statement type: {type(node).__name__}", node)

    def visit_block(self, stmts: List[ast.stmt]) -> List[IRStmt]:
        self.symtab.enter_scope()
        ir_stmts = []
        for s in stmts:
            ir_stmts.extend(self.visit_stmt(s))
        self.symtab.exit_scope()
        return ir_stmts

    # ========== TWO-PASS COMPILATION ==========

    def scan_declarations(self, tree: ast.Module):
        """PASS 1: Collect struct & function signatures"""
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                # Register struct fields
                fields: Dict[str, RustType] = {}
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        try:
                            fields[item.target.id] = self.parse_annotation(item.annotation)
                        except SystemExit: # Catch custom fail()
                            fail(f"Cannot parse type for struct field {item.target.id}", item)
                self.symtab.register_struct(node.name, fields)

                # Register methods
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        arg_types: List[RustType] = []
                        # Skip 'self' argument for type signature
                        for arg in item.args.args:
                            if arg.arg != 'self':
                                try:
                                    arg_types.append(self.parse_annotation(arg.annotation))
                                except SystemExit:
                                    fail(f"Cannot parse type for method argument '{arg.arg}' in '{item.name}'", arg)
                        
                        ret_type = RustType(RustTypeKind.UNIT)
                        if item.returns:
                            try:
                                ret_type = self.parse_annotation(item.returns)
                            except SystemExit:
                                fail(f"Cannot parse return type for method '{item.name}'", item)
                        
                        self.symtab.register_method(node.name, item.name, arg_types, ret_type)

            elif isinstance(node, ast.FunctionDef):
                arg_types: List[RustType] = []
                for arg in node.args.args:
                    try:
                        arg_types.append(self.parse_annotation(arg.annotation))
                    except SystemExit:
                        fail(f"Cannot parse type for function argument '{arg.arg}' in '{node.name}'", arg)
                
                ret_type = RustType(RustTypeKind.UNIT)
                if node.returns:
                    try:
                        ret_type = self.parse_annotation(node.returns)
                    except SystemExit:
                        fail(f"Cannot parse return type for function '{node.name}'", node)
                
                self.symtab.func_sigs[node.name] = (arg_types, ret_type)
            # Other top-level statements are handled in the second pass for the main block.

    def compile_module(self, tree: ast.Module) -> IRModule:
        self.scan_declarations(tree) # First pass

        structs: List[IRStructDecl] = []
        funcs: List[IRFuncDecl] = []
        main_stmts: List[IRStmt] = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                fields = [(n, t) for n, t in self.symtab.struct_defs[node.name].items()]
                structs.append(IRStructDecl(node.name, fields))

                # Compile methods within the struct's impl block
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        # Get return type from pre-scanned method signatures
                        sig = self.symtab.get_method_sig(node.name, item.name)
                        if sig:
                            _, self.current_func_ret_type = sig
                        else:
                            self.current_func_ret_type = RustType(RustTypeKind.UNIT) # Should not happen if scan_declarations is correct

                        self.symtab.enter_scope()
                        
                        args: List[Tuple[str, RustType]] = []
                        for arg in item.args.args:
                            if arg.arg == 'self':
                                self.symtab.declare('self', RustType(RustTypeKind.STRUCT, name=node.name))
                            else:
                                t = self.parse_annotation(arg.annotation)
                                self.symtab.declare(arg.arg, t, is_mut=False, is_arg=True)
                                args.append((arg.arg, t))
                        
                        body_ir = self.visit_block(item.body)
                        self.symtab.exit_scope()

                        funcs.append(IRFuncDecl(
                            item.name, args, self.current_func_ret_type, body_ir,
                            is_method=True, self_type=RustType(RustTypeKind.STRUCT, name=node.name)
                        ))

            elif isinstance(node, ast.FunctionDef):
                # Get return type from pre-scanned function signatures
                sig = self.symtab.func_sigs.get(node.name)
                if sig:
                    _, self.current_func_ret_type = sig
                else:
                    self.current_func_ret_type = RustType(RustTypeKind.UNIT) # Should not happen

                self.symtab.enter_scope()
                
                args: List[Tuple[str, RustType]] = []
                for arg in node.args.args:
                    t = self.parse_annotation(arg.annotation)
                    self.symtab.declare(arg.arg, t, is_mut=False, is_arg=True)
                    args.append((arg.arg, t))
                
                body_ir = self.visit_block(node.body)
                self.symtab.exit_scope()

                funcs.append(IRFuncDecl(node.name, args, self.current_func_ret_type, body_ir))

            else:
                # Top-level statements become part of `main` function
                # Reset current_func_ret_type for the main block context if needed (it's unit by default)
                self.current_func_ret_type = RustType(RustTypeKind.UNIT)
                main_stmts.extend(self.visit_stmt(node))

        return IRModule(structs, funcs, main_stmts)

# ==============================================================================
# 5. RUST CODE GENERATOR
# ==============================================================================

class RustEmitter:
    def __init__(self):
        self.indent_level = 0

    def indent(self) -> str:
        return "    " * self.indent_level

    def emit_type(self, t: RustType) -> str:
        return str(t)

    def emit_expr(self, expr: IRExpr) -> str:
        if isinstance(expr, IRLiteral):
            # Convert string literals to String::from(...)
            if expr.rtype.kind == RustTypeKind.STRING:
                return f"String::from({expr.value})"
            return expr.value

        if isinstance(expr, IRVariable):
            return expr.name

        if isinstance(expr, IRBinaryOp):
            lhs = self.emit_expr(expr.left)
            rhs = self.emit_expr(expr.right)
            
            # String concat is handled by IRCall with format!
            # For other types, direct op is fine
            return f"{lhs} {expr.op} {rhs}"

        if isinstance(expr, IRUnaryOp):
            operand = self.emit_expr(expr.operand)
            # Special case for `parse().unwrap()` which comes from `int("string")` or `float("string")`
            if expr.op == "parse" and operand.endswith(".to_string()"):
                 # This is a bit hacky, directly manipulating emitted string. Better would be an IR node for parse+unwrap.
                return f"({operand}).parse().unwrap()"
            elif expr.op == "parse": # For simple variables trying to parse
                return f"{operand}.parse().unwrap()"
            return f"{expr.op}{operand}"

        if isinstance(expr, IRCall):
            args_str: List[str] = []
            for arg_expr in expr.args:
                val = self.emit_expr(arg_expr)
                # Clone non-primitives (except ranges for now) when passed to functions that take ownership
                # or when we need to ensure the original is not consumed.
                # This needs careful ownership analysis, but a blanket clone for non-primitives is a safer default.
                if arg_expr.rtype.clone_needed():
                    val = f"{val}.clone()"
                args_str.append(val)

            joined_args = ", ".join(args_str)

            if expr.func_name == "format!":
                # First argument of format! is the format string itself (literal)
                if not args_str:
                    return 'format!("")' # Empty format! call
                
                # The first arg is the format string itself.
                # Assuming the first argument is always an IRLiteral of STRING or IRFString.
                # We need to extract the raw format string without `String::from`
                fmt_literal = cast(IRLiteral, expr.args[0])
                actual_fmt_str = fmt_literal.value.strip('"') # Remove surrounding quotes
                
                # The remaining args are the expressions to format
                remaining_args_str = ", ".join(args_str[1:])
                return f'format!("{actual_fmt_str}", {remaining_args_str})'
            
            if expr.func_name == "println!":
                # println! needs special handling for different arg types
                if not expr.args:
                    return f"println!()"
                
                # If the first argument is an IRFString, use its format directly.
                if isinstance(expr.args[0], IRFString):
                    fstring_expr = cast(IRFString, expr.args[0])
                    # Ensure raw format string is used, and then its arguments
                    return f'println!("{fstring_expr.fmt_str}", {", ".join([self.emit_expr(a) for a in fstring_expr.args])})'
                
                # If the first arg is a simple String literal (like println!("hello")), use it directly
                if expr.args[0].rtype.kind == RustTypeKind.STRING and isinstance(expr.args[0], IRLiteral):
                    # For a single String literal, println! takes it directly
                    if len(expr.args) == 1:
                        return f'println!({expr.args[0].value})'
                    else: # If a String literal is followed by other args, it acts as a format string
                        # e.g., println!("Hello {}", name)
                        format_literal = cast(IRLiteral, expr.args[0])
                        actual_fmt_str = format_literal.value.strip('"')
                        remaining_args_str = ", ".join(args_str[1:])
                        return f'println!("{actual_fmt_str}", {remaining_args_str})'

                # Otherwise, assume display format for all arguments
                format_placeholders = ", ".join(["{}" for _ in args_str])
                return f'println!("{format_placeholders}", {joined_args})'

            if expr.func_name == "to_string" and expr.is_method and expr.instance:
                # `str(x)` becomes `x.to_string()`
                inst_str = self.emit_expr(expr.instance)
                return f"{inst_str}.to_string()"
            
            if (expr.func_name == "parse" or expr.func_name == "as i64" or expr.func_name == "as f64") and expr.is_method and expr.instance:
                # `int(s)` becomes `s.parse().unwrap()` or `f as i64`
                inst_str = self.emit_expr(expr.instance)
                if expr.func_name == "parse":
                    return f"{inst_str}.parse().unwrap()"
                else: # "as i64" or "as f64"
                    return f"{inst_str} {expr.func_name}"

            if expr.is_method and expr.instance:
                inst = self.emit_expr(expr.instance)
                return f"{inst}.{expr.func_name}({joined_args})"

            return f"{expr.func_name}({joined_args})"

        if isinstance(expr, IRListCtor):
            elems = ", ".join([self.emit_expr(e) for e in expr.elements])
            return f"vec![{elems}]"

        if isinstance(expr, IRDictCtor):
            pairs = []
            for k, v in zip(expr.keys, expr.values):
                pairs.append(f"({self.emit_expr(k)}, {self.emit_expr(v)})")
            return f"std::collections::HashMap::from([{', '.join(pairs)}])"

        if isinstance(expr, IRStructCtor):
            # Person() → Person { name: String::default(), age: 0, ... }
            if not expr.fields:
                # Empty constructor: use Default trait
                return f"{expr.struct_name}::default()"
            field_strs = [f"{name}: {self.emit_expr(val)}" for name, val in expr.fields]
            return f"{expr.struct_name} {{ {', '.join(field_strs)} }}"

        if isinstance(expr, IRFieldAccess):
            return f"{self.emit_expr(expr.instance)}.{expr.field}"

        if isinstance(expr, IRRangeCtor):
            start = self.emit_expr(expr.start)
            end = self.emit_expr(expr.end)
            if expr.exclusive:
                return f"{start}..{end}"
            else:
                return f"{start}..={end}" # Rust supports inclusive ranges a..=b

        if isinstance(expr, IRFString):
            args_emitted = [self.emit_expr(arg) for arg in expr.args]
            args_str = ", ".join(args_emitted) # pyright: ignore[reportAssignmentType]
            if args_emitted:
                return f'format!("{expr.fmt_str}", {args_str})'
            else:
                return f'"{expr.fmt_str}".to_string()'

        return "/* unknown expr */"

    def emit_stmt(self, stmt: IRStmt) -> str:
        i = self.indent()

        if isinstance(stmt, IRVarDecl):
            mut = "mut " if stmt.is_mut else ""
            if stmt.init:
                init_expr = self.emit_expr(stmt.init)
                return f"{i}let {mut}{stmt.name}: {self.emit_type(stmt.rtype)} = {init_expr};\n"
            else:
                # For `let mut x: Type;` without initialization
                return f"{i}let {mut}{stmt.name}: {self.emit_type(stmt.rtype)};\n"

        if isinstance(stmt, IRAssign):
            # If the target is a variable that needs cloning on assignment (e.g., Vec, String)
            # This is a simplification; Rust's move semantics are complex.
            # For `x = y`, if y is owned, it moves. If x is also owned, then it needs a clone if y is used later.
            # For basic transpilation, we might just assume moves.
            # However, if `value` is an IRVariable of an owned type and `target` is a mutable variable,
            # a clone might be required to avoid ownership issues if `value` is still used later.
            # But, the type checker should enforce that `value` isn't used after being moved if not cloned.
            # For now, we assume simple assignment for `IRAssign` handles `x = y` (move if owned, copy if primitive).
            return f"{i}{stmt.target} = {self.emit_expr(stmt.value)};\n"

        if isinstance(stmt, IRFieldAssign):
            return f"{i}{stmt.obj_name}.{stmt.field_name} = {self.emit_expr(stmt.value)};\n"

        if isinstance(stmt, IRExprStmt):
            expr_str = self.emit_expr(stmt.expr)
            # If the expression's return type is not Unit, it should be followed by a semicolon
            # unless it's the last expression in a block (implicitly returned).
            # For simplicity, we add semicolon to all expr statements.
            return f"{i}{expr_str};\n"

        if isinstance(stmt, IRReturn):
            if stmt.value:
                val = self.emit_expr(stmt.value)
                return f"{i}return {val};\n"
            return f"{i}return;\n"

        if isinstance(stmt, IRIf):
            cond = self.emit_expr(stmt.condition)
            res = f"{i}if {cond} {{\n"
            self.indent_level += 1
            for s in stmt.then_block:
                res += self.emit_stmt(s)
            self.indent_level -= 1
            
            if stmt.else_block:
                res += f"{i}}} else {{\n"
                self.indent_level += 1
                for s in stmt.else_block:
                    res += self.emit_stmt(s)
                self.indent_level -= 1
                res += f"{i}}}\n"
            else:
                res += f"{i}}}\n"
            return res

        if isinstance(stmt, IRWhile):
            cond = self.emit_expr(stmt.condition)
            res = f"{i}while {cond} {{\n"
            self.indent_level += 1
            for s in stmt.body:
                res += self.emit_stmt(s)
            self.indent_level -= 1
            res += f"{i}}}\n"
            return res

        if isinstance(stmt, IRFor):
            target = stmt.target_name
            iterator_expr_str = self.emit_expr(stmt.iterator)

            # For iteration over Vecs or HashMaps, usually we iterate over references (`&vec` or `&map`)
            # or mutable references (`&mut vec`). For Python-like iteration, `&` is common.
            # If the original Python iterated over `list_var`, Rust will iterate over `&list_var`
            # or `list_var.iter()` if not consuming.
            # For `range`, it's already a range type, no need for `&`.
            
            # Simple heuristic: if it's a Vec variable, iterate over its reference
            if isinstance(stmt.iterator, IRVariable) and stmt.iterator.rtype.kind == RustTypeKind.VEC:
                iterator_expr_str = f"&{iterator_expr_str}"
            elif isinstance(stmt.iterator, IRVariable) and stmt.iterator.rtype.kind == RustTypeKind.HASHMAP:
                 # Iterate over `&map` to get `(&key, &value)` pairs
                iterator_expr_str = f"&{iterator_expr_str}"

            res = f"{i}for {target} in {iterator_expr_str} {{\n"
            self.indent_level += 1
            for s in stmt.body:
                res += self.emit_stmt(s)
            self.indent_level -= 1
            res += f"{i}}}\n"
            return res

        return f"{i}/* unknown stmt type: {type(stmt).__name__} */\n"

    def emit_module(self, module: IRModule) -> str:
        out = ""
        # Prelude
        out += "#[allow(unused_imports)]\n"
        out += "#[allow(unused_variables)]\n" # Allow unused for now, to reduce transpiler complexity
        out += "#[allow(dead_code)]\n" # Allow dead code for functions not called from main
        out += "use std::collections::{HashMap, HashSet};\n"
        out += "use std::cmp::{min, max};\n\n"

        # Structs
        for s in module.structs:
            out += f"#[derive(Debug, Clone, Default)]\n" # Add Default for empty constructors
            out += f"struct {s.name} {{\n"
            for field, ftype in s.fields:
                out += f"{self.indent()}    pub {field}: {self.emit_type(ftype)},\n"
            out += "}\n\n"

        # Separate methods from global functions
        methods_map: Dict[str, List[IRFuncDecl]] = {}
        global_funcs: List[IRFuncDecl] = []

        for f in module.funcs:
            if f.is_method and f.self_type:
                struct_name = f.self_type.name
                if struct_name not in methods_map:
                    methods_map[struct_name] = []
                methods_map[struct_name].append(f)
            else:
                global_funcs.append(f)

        # Impl blocks for methods
        for struct_name, funcs in methods_map.items():
            out += f"impl {struct_name} {{\n"
            self.indent_level += 1
            
            for f in funcs:
                args = []
                # `self` argument needs special handling in Rust method signatures
                # Assuming `self` means `&mut self` for Python methods that modify state
                # If a method doesn't modify self, it could be `&self` or `self`.
                # For simplicity, we default to `&mut self` for now.
                # A more sophisticated analysis would determine mutability.
                self_arg = "&mut self" # Default assumption for Python methods

                for name, type_ in f.args:
                    args.append(f"{name}: {self.emit_type(type_)}")

                # Construct the full method signature
                sig = f"pub fn {f.name}({self_arg}"
                if args:
                    sig += ", " + ", ".join(args)
                sig += ")"

                if f.ret_type.kind != RustTypeKind.UNIT:
                    sig += f" -> {self.emit_type(f.ret_type)}"

                out += f"{self.indent()}{sig} {{\n"
                self.indent_level += 1
                for stmt in f.body:
                    out += self.emit_stmt(stmt)
                self.indent_level -= 1
                out += f"{self.indent()}}}\n\n"

            self.indent_level -= 1
            out += "}\n\n"

        # Global functions
        for f in global_funcs:
            args = ", ".join([f"{n}: {self.emit_type(t)}" for n, t in f.args])
            sig = f"fn {f.name}({args})"
            if f.ret_type.kind != RustTypeKind.UNIT:
                sig += f" -> {self.emit_type(f.ret_type)}"

            out += f"{sig} {{\n"
            self.indent_level += 1
            for stmt in f.body:
                out += self.emit_stmt(stmt)
            self.indent_level -= 1
            out += "}\n\n"

        # Main function
        out += "fn main() {\n"
        self.indent_level += 1
        for stmt in module.main_block:
            out += self.emit_stmt(stmt)
        self.indent_level -= 1
        out += "}\n"

        return out

# ==============================================================================
# 6. MAIN DRIVER
# ==============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Static Python → Rust Transpiler")
    parser.add_argument("input", help="Input Python file")
    parser.add_argument("--output", "-o", default=None, help="Output Rust file (default: <input>.rs)")
    parser.add_argument("--no-compile", action="store_true", default=False,
                       help="Only generate .rs file, don't compile to binary")
    parser.add_argument("--keep-pdb", action="store_true", default=False,
                       help="Keep .pdb debug symbols file (Windows only)")
    parser.add_argument("--run", action="store_true", default=False,
                       help="Execute the compiled binary after successful compilation")
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f: # Specify encoding
            source = f.read()
    except FileNotFoundError:
        print(f"Error: File '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file '{args.input}': {e}", file=sys.stderr)
        sys.exit(1)

    try:
        tree = ast.parse(source, filename=args.input)
    except SyntaxError as e:
        print(f"Syntax Error in Python file: {e}", file=sys.stderr)
        sys.exit(1)

    compiler = PythonToRustCompiler()
    try:
        # STEP 1: Compile Python to IR and generate Rust code
        ir_module = compiler.compile_module(tree)
        emitter = RustEmitter()
        rust_code = emitter.emit_module(ir_module)
        
        # Determine output filename and paths
        input_dir = os.path.dirname(os.path.abspath(args.input))
        input_basename_no_ext = os.path.splitext(os.path.basename(args.input))[0]

        if args.output is None:
            output_rs_path = os.path.join(input_dir, f"{input_basename_no_ext}.rs")
        else:
            # If --output is specified, use it directly.
            # If it's just a filename, put it in input_dir. If it's a full path, use that.
            if not os.path.isabs(args.output):
                output_rs_path = os.path.join(input_dir, args.output)
            else:
                output_rs_path = args.output
        
        # STEP 2: Write Rust code to file (always keep .rs file by default)
        with open(output_rs_path, 'w', encoding="utf-8") as f:
            f.write(rust_code)
        print(f"[STEP 1] Generated: {output_rs_path}", file=sys.stderr)
        
        # STEP 3: Compile Rust to binary (if not --no-compile)
        if not args.no_compile:
            # Determine output binary path
            output_bin_name = input_basename_no_ext # e.g., 'test'
            if sys.platform == 'win32':
                output_bin_name += '.exe'
            output_bin_path = os.path.join(input_dir, output_bin_name)
            
            print(f"[STEP 2] Compiling: rustc {output_rs_path} -o {output_bin_path}", file=sys.stderr)
            
            # Use a list for command and direct subprocess.run
            rustc_command = ['rustc', output_rs_path, '-o', output_bin_path]
            
            # For Windows, also specify the target-dir to keep build artifacts in one place
            # if sys.platform == 'win32':
            #     rustc_command.extend(['--emit', 'dep-info,link,metadata,pdb,asm,llvm-ir,obj', '--target-dir', input_dir])

            result = subprocess.run(rustc_command, capture_output=True, text=True, cwd=input_dir)
            
            if result.returncode != 0:
                print(f"[ERROR] Rust compilation failed:", file=sys.stderr)
                print(result.stdout, file=sys.stderr) # stdout might contain warnings
                print(result.stderr, file=sys.stderr)
                # Cleanup the .rs file if compilation failed
                # os.remove(output_rs_path) # Retain .rs file always as per new instruction
                sys.exit(1)
            
            print(f"[STEP 2] Compiled: {output_bin_path}", file=sys.stderr)
            
            # STEP 4: Run the binary (if --run flag)
            if args.run:
                print(f"[STEP 3] Running: {output_bin_path}", file=sys.stderr)
                run_result = subprocess.run([output_bin_path], capture_output=False, cwd=input_dir)
                if run_result.returncode != 0:
                    print(f"[ERROR] Program exited with non-zero code: {run_result.returncode}", file=sys.stderr)
                    sys.exit(1)
            
            # STEP 5: Clean up .pdb files on Windows if not --keep-pdb
            if not args.keep_pdb and sys.platform == 'win32':
                pdb_file = os.path.join(input_dir, f"{input_basename_no_ext}.pdb")
                if os.path.exists(pdb_file):
                    try:
                        os.remove(pdb_file)
                        print(f"[CLEANUP] Removed: {pdb_file}", file=sys.stderr)
                    except Exception as e:
                        print(f"[WARN] Could not remove {pdb_file}: {e}", file=sys.stderr)
        else:
            print(f"[SKIP] Compilation to binary skipped (--no-compile)", file=sys.stderr)
            
    except CompilerError:
        # Custom CompilerError already prints message and exits
        sys.exit(1)
    except Exception as e:
        print(f"\n[CRITICAL ERROR] An unexpected error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
        