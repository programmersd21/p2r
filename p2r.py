import ast
import glob
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import NoReturn, cast


class RustTypeKind(Enum):
    UNIT = auto()
    VOID = auto()
    BOOL = auto()
    I64 = auto()
    F64 = auto()
    STRING = auto()
    VEC = auto()
    HASHMAP = auto()
    STRUCT = auto()
    OPTION = auto()
    RANGE = auto()


@dataclass
class RustType:
    kind: RustTypeKind
    name: str = ""
    inner: list["RustType"] = field(default_factory=list)

    def __repr__(self) -> str:
        m = {
            RustTypeKind.UNIT: "()",
            RustTypeKind.VOID: "()",
            RustTypeKind.BOOL: "bool",
            RustTypeKind.I64: "i64",
            RustTypeKind.F64: "f64",
            RustTypeKind.STRING: "String",
            RustTypeKind.VEC: f"Vec<{self.inner[0]}>" if self.inner else "Vec<?>",
            RustTypeKind.HASHMAP: (
                f"HashMap<{self.inner[0]},{self.inner[1]}>"
                if len(self.inner) >= 2
                else "HashMap<?,?>"
            ),
            RustTypeKind.STRUCT: self.name,
            RustTypeKind.OPTION: (
                f"Option<{self.inner[0]}>" if self.inner else "Option<?>"
            ),
            RustTypeKind.RANGE: "std::ops::Range<i64>",
        }
        return m.get(self.kind, "?")

    def unify(self, o: "RustType") -> bool:
        return (self.kind == o.kind if self.kind == o.kind else False) and (
            self.name == o.name
            if self.kind == RustTypeKind.STRUCT
            else (
                all(s.unify(x) for s, x in zip(self.inner, o.inner))
                if len(self.inner) == len(o.inner)
                else False
            )
        )


@dataclass
class IRExpr:
    rtype: RustType


@dataclass
class IRLiteral(IRExpr):
    value: str
    is_input_prompt: bool = False


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
    args: list[IRExpr]
    is_method: bool = False
    instance: IRExpr | None = None


@dataclass
class IRFieldAccess(IRExpr):
    instance: IRExpr
    field: str


@dataclass
class IRListCtor(IRExpr):
    elements: list[IRExpr]


@dataclass
class IRDictCtor(IRExpr):
    keys: list[IRExpr]
    values: list[IRExpr]


@dataclass
class IRStructCtor(IRExpr):
    struct_name: str
    fields: list[tuple[str, IRExpr]]


@dataclass
class IRFString(IRExpr):
    fmt_str: str
    args: list[IRExpr]


@dataclass
class IRRangeCtor(IRExpr):
    start: IRExpr
    end: IRExpr
    exclusive: bool = True


@dataclass
class IRIndexAccess(IRExpr):
    container: IRExpr
    index: IRExpr


@dataclass
class IRStmt:
    pass


@dataclass
class IRVarDecl(IRStmt):
    name: str
    rtype: RustType
    is_mut: bool
    init: IRExpr | None


@dataclass
class IRAssign(IRStmt):
    target: str
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
    then_block: list[IRStmt]
    else_block: list[IRStmt] | None = None


@dataclass
class IRWhile(IRStmt):
    condition: IRExpr
    body: list[IRStmt]


@dataclass
class IRFor(IRStmt):
    target_name: str
    iterator: IRExpr
    body: list[IRStmt]


@dataclass
class IRReturn(IRStmt):
    value: IRExpr | None = None


@dataclass
class IRFuncDecl:
    name: str
    args: list[tuple[str, RustType]]
    ret_type: RustType
    body: list[IRStmt]
    is_method: bool = False
    self_type: RustType | None = None


@dataclass
class IRStructDecl:
    name: str
    fields: list[tuple[str, RustType]]


@dataclass
class IRModule:
    structs: list[IRStructDecl]
    funcs: list[IRFuncDecl]
    main_block: list[IRStmt]


def fail(m: str, n: ast.AST | None = None) -> NoReturn:
    print(f"\n❌ Line {getattr(n,'lineno','?')}: {m}\n", file=sys.stderr)
    sys.exit(1)


@dataclass
class SymbolInfo:
    name: str
    rtype: RustType
    is_mut: bool = False
    is_arg: bool = False


class SymbolTable:
    def __init__(self):
        self.scopes: list[dict[str, SymbolInfo]] = [{}]
        self.struct_defs: dict[str, dict[str, RustType]] = {}
        self.method_sigs: dict[tuple[str, str], tuple[list[RustType], RustType]] = {}
        self.func_sigs: dict[str, tuple[list[RustType], RustType]] = {}

    def enter_scope(self) -> None:
        self.scopes.append({})

    def exit_scope(self) -> None:
        self.scopes.pop() if len(self.scopes) > 1 else None

    def declare(
        self, name: str, rtype: RustType, is_mut: bool = False, is_arg: bool = False
    ) -> None:
        self.scopes[-1][name] = SymbolInfo(name, rtype, is_mut, is_arg)

    def lookup(self, name: str) -> SymbolInfo | None:
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def register_struct(self, name: str, fields: dict[str, RustType]) -> None:
        self.struct_defs = getattr(self, "struct_defs", {}) | {name: fields}

    def get_struct_field_type(self, sn: str, f: str) -> RustType | None:
        return getattr(self, "struct_defs", {}).get(sn, {}).get(f)

    def register_method(
        self, sn: str, mn: str, at: list[RustType], rt: RustType
    ) -> None:
        self.method_sigs = getattr(self, "method_sigs", {}) | {(sn, mn): (at, rt)}

    def get_method_sig(
        self, sn: str, mn: str
    ) -> tuple[list[RustType], RustType] | None:
        return getattr(self, "method_sigs", {}).get((sn, mn))


class Compiler:
    def __init__(self):
        self.symtab = SymbolTable()
        self.symtab.struct_defs = {}
        self.symtab.method_sigs = {}
        self.symtab.func_sigs = {}
        self.current_func_ret_type = RustType(RustTypeKind.UNIT)
        # Register built-in __name__ variable
        self.symtab.declare("__name__", RustType(RustTypeKind.STRING), is_mut=False)

    def parse_anno(self, n: ast.AST | None) -> RustType:
        if n is None:
            fail("Type annotation required", n)

        if isinstance(n, ast.Constant) and n.value is None:
            return RustType(RustTypeKind.VOID)

        if isinstance(n, ast.Name):
            m: dict[str, RustType] = {
                "int": RustType(RustTypeKind.I64),
                "float": RustType(RustTypeKind.F64),
                "bool": RustType(RustTypeKind.BOOL),
                "str": RustType(RustTypeKind.STRING),
                "None": RustType(RustTypeKind.VOID),
            }
            if n.id in m:
                return m[n.id]
            if n.id in self.symtab.struct_defs:
                return RustType(RustTypeKind.STRUCT, name=n.id)
            fail(f"Unknown type {n.id}", n)

        if isinstance(n, ast.Subscript):
            if not isinstance(n.value, ast.Name):
                fail("Bad subscript", n)

            base = n.value.id

            if base in ("List", "list"):
                return RustType(RustTypeKind.VEC, inner=[self.parse_anno(n.slice)])

            if base in ("Dict", "dict"):
                if isinstance(n.slice, ast.Tuple):
                    dims = [self.parse_anno(e) for e in n.slice.elts]
                else:
                    dims = [self.parse_anno(n.slice)]
                if len(dims) != 2:
                    fail("Dict needs [K,V]", n)
                return RustType(RustTypeKind.HASHMAP, inner=dims)

        fail("Bad type", n)

    def infer_lit(self, n: ast.Constant) -> RustType:
        if isinstance(n.value, bool):
            return RustType(RustTypeKind.BOOL)
        if isinstance(n.value, int):
            return RustType(RustTypeKind.I64)
        if isinstance(n.value, float):
            return RustType(RustTypeKind.F64)
        if isinstance(n.value, str):
            return RustType(RustTypeKind.STRING)
        if n.value is None:
            return RustType(RustTypeKind.UNIT)
        fail("Bad literal", n)

    def rust_str(self, s: str) -> str:
        return (
            f'"{s.replace(chr(92),chr(92)+chr(92)).replace(chr(34),chr(92)+chr(34))}"'
        )

    def visit_expr(self, n: ast.expr | None) -> IRExpr:
        if n is None:
            fail("Unexpected None expr")
        if isinstance(n, ast.Constant):
            t = self.infer_lit(n)
            v = (
                str(n.value).lower()
                if t.kind == RustTypeKind.BOOL
                else (
                    self.rust_str(str(n.value))
                    if t.kind == RustTypeKind.STRING
                    else "()" if n.value is None else str(n.value)
                )
            )
            return IRLiteral(t, v)
        if isinstance(n, ast.Name):
            s = self.symtab.lookup(n.id)
            if s is None:
                fail(f"Undefined {n.id}", n)
            return IRVariable(s.rtype, n.id)
        if isinstance(n, ast.BinOp):
            l, r = self.visit_expr(n.left), self.visit_expr(n.right)
            fail("Type mismatch", n) if not l.rtype.unify(r.rtype) else None
            m = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
            o = m.get(type(n.op))
            fail("Bad op", n) if not o else None
            return IRBinaryOp(l.rtype, l, o, r)
        if isinstance(n, ast.Compare):
            fail("Chained comp unsupported", n) if len(n.ops) > 1 else None
            l, r = self.visit_expr(n.left), self.visit_expr(n.comparators[0])
            fail("Type mismatch", n) if not l.rtype.unify(r.rtype) else None
            m = {
                ast.Eq: "==",
                ast.NotEq: "!=",
                ast.Lt: "<",
                ast.LtE: "<=",
                ast.Gt: ">",
                ast.GtE: ">=",
            }
            op_node = n.ops[0]
            o = m.get(type(op_node))
            if o is None:
                fail("Bad op", n)
            return IRBinaryOp(RustType(RustTypeKind.BOOL), l, o, r)
        if isinstance(n, ast.BoolOp):
            o = "&&" if isinstance(n.op, ast.And) else "||"
            c = self.visit_expr(n.values[0])
            fail("Bad bool", n) if c.rtype.kind != RustTypeKind.BOOL else None
            for v in n.values[1:]:
                x = self.visit_expr(v)
                fail("Bad bool", v) if x.rtype.kind != RustTypeKind.BOOL else None
                c = IRBinaryOp(RustType(RustTypeKind.BOOL), c, o, x)
            return c
        if isinstance(n, ast.UnaryOp):
            op = self.visit_expr(n.operand)
            if isinstance(n.op, ast.Not):
                fail("Not bool", n) if op.rtype.kind != RustTypeKind.BOOL else None
                return IRUnaryOp(RustType(RustTypeKind.BOOL), "!", op)
            if isinstance(n.op, ast.USub):
                (
                    fail("Unary - not num", n)
                    if op.rtype.kind not in {RustTypeKind.I64, RustTypeKind.F64}
                    else None
                )
                return IRUnaryOp(op.rtype, "-", op)
            fail("Bad unary", n)
        if isinstance(n, ast.Call):
            return self.visit_call(n)
        if isinstance(n, ast.List):
            fail("Empty list", n) if not n.elts else None
            e = [self.visit_expr(x) for x in n.elts]
            t = e[0].rtype
            fail("Unhomog", n) if not all(x.rtype.unify(t) for x in e) else None
            return IRListCtor(RustType(RustTypeKind.VEC, inner=[t]), e)
        if isinstance(n, ast.Dict):
            fail("Empty dict", n) if not n.keys else None
            k = [self.visit_expr(x) for x in n.keys]
            v = [self.visit_expr(x) for x in n.values]
            kt = k[0].rtype
            vt = v[0].rtype
            fail("Unhomog keys", n) if not all(x.rtype.unify(kt) for x in k) else None
            fail("Unhomog vals", n) if not all(x.rtype.unify(vt) for x in v) else None
            return IRDictCtor(RustType(RustTypeKind.HASHMAP, inner=[kt, vt]), k, v)
        if isinstance(n, ast.Subscript):
            c, i = self.visit_expr(n.value), self.visit_expr(n.slice)
            if c.rtype.kind == RustTypeKind.VEC:
                fail("Index not i64", n) if i.rtype.kind != RustTypeKind.I64 else None
                return IRIndexAccess(c.rtype.inner[0], c, i)
            if c.rtype.kind == RustTypeKind.HASHMAP:
                fail("Key type bad", n) if not i.rtype.unify(c.rtype.inner[0]) else None
                return IRIndexAccess(
                    RustType(RustTypeKind.OPTION, inner=[c.rtype.inner[1]]), c, i
                )
            fail("Can't index", n)
        if isinstance(n, ast.Attribute):
            i = self.visit_expr(n.value)
            fail("Not struct", n) if i.rtype.kind != RustTypeKind.STRUCT else None
            f = self.symtab.get_struct_field_type(i.rtype.name, n.attr)
            fail("No field", n) if not f else None
            return IRFieldAccess(f, i, n.attr)
        if isinstance(n, ast.JoinedStr):
            return self.visit_fstr(n)
        fail("Bad expr", n)

    def visit_call(self, n: ast.Call) -> IRExpr:
        fn, ae = n.func, [self.visit_expr(a) for a in n.args]
        if isinstance(fn, ast.Attribute):
            inst, mn = self.visit_expr(fn.value), fn.attr
            if inst.rtype.kind == RustTypeKind.VEC:
                if mn == "append":
                    fail("append 1", n) if len(ae) != 1 else None
                    (
                        fail("Type bad", n)
                        if not ae[0].rtype.unify(inst.rtype.inner[0])
                        else None
                    )
                    return IRCall(RustType(RustTypeKind.UNIT), "push", ae, True, inst)
                if mn == "pop":
                    fail("pop 0", n) if len(ae) != 0 else None
                    return IRCall(
                        RustType(RustTypeKind.OPTION, inner=inst.rtype.inner),
                        "pop",
                        ae,
                        True,
                        inst,
                    )
                fail("Bad method", n)
            if inst.rtype.kind == RustTypeKind.STRING:
                if mn in ("upper", "lower", "strip", "trim"):
                    fail("No args", n) if len(ae) != 0 else None
                    rm = {
                        "upper": "to_uppercase",
                        "lower": "to_lowercase",
                        "strip": "trim",
                        "trim": "trim",
                    }.get(mn)

                    if rm is None:
                        fail("Bad string method", n)

                    return IRCall(RustType(RustTypeKind.STRING), rm, ae, True, inst)
            if inst.rtype.kind == RustTypeKind.STRUCT:
                # Handle struct method calls
                sn = inst.rtype.name
                sig = self.symtab.get_method_sig(sn, mn)
                if sig:
                    arg_types, ret_type = sig
                    fail("Arg count", n) if len(ae) != len(arg_types) else None
                    for i, (ae_i, et) in enumerate(zip(ae, arg_types)):
                        fail("Arg type", n) if not ae_i.rtype.unify(et) else None
                    return IRCall(ret_type, mn, ae, True, inst)
                fail(f"No method {mn} on {sn}", n)
            fail("Bad method", n)
        if isinstance(fn, ast.Name):
            fname = fn.id
            if fname == "print":
                return IRCall(RustType(RustTypeKind.UNIT), "println!", ae)
            if fname == "len":
                fail("len 1", n) if len(ae) != 1 else None
                (
                    fail("Bad type", n)
                    if ae[0].rtype.kind
                    not in {RustTypeKind.VEC, RustTypeKind.STRING, RustTypeKind.HASHMAP}
                    else None
                )
                return IRCall(RustType(RustTypeKind.I64), "len", ae, True, ae[0])
            if fname == "str":
                fail("str 1", n) if len(ae) != 1 else None
                return IRCall(
                    RustType(RustTypeKind.STRING), "to_string", ae, True, ae[0]
                )
            if fname == "int":
                fail("int 1", n) if len(ae) != 1 else None
                if ae[0].rtype.kind == RustTypeKind.STRING:
                    return IRCall(RustType(RustTypeKind.I64), "parse", ae, True, ae[0])
                if ae[0].rtype.kind == RustTypeKind.F64:
                    return IRCall(RustType(RustTypeKind.I64), "as_i64", ae, True, ae[0])
                fail("Bad int", n)
            if fname == "float":
                fail("float 1", n) if len(ae) != 1 else None
                if ae[0].rtype.kind == RustTypeKind.STRING:
                    return IRCall(RustType(RustTypeKind.F64), "parse", ae, True, ae[0])
                if ae[0].rtype.kind == RustTypeKind.I64:
                    return IRCall(RustType(RustTypeKind.F64), "as_f64", ae, True, ae[0])
                fail("Bad float", n)
            if fname == "input":
                if len(ae) == 0:
                    return IRCall(
                        RustType(RustTypeKind.STRING),
                        "__read_input",
                        [
                            IRLiteral(
                                RustType(RustTypeKind.STRING),
                                '""',
                                is_input_prompt=True,
                            )
                        ],
                    )
                if len(ae) == 1:
                    arg = ae[0]
                    if (
                        isinstance(arg, IRLiteral)
                        and arg.rtype.kind == RustTypeKind.STRING
                    ):
                        arg.is_input_prompt = True
                    return IRCall(RustType(RustTypeKind.STRING), "__read_input", [arg])
                fail("input takes 0 or 1 args", n)
            if fname == "range":
                fail("range args", n) if not ae else None
                if len(ae) == 1:
                    se, ee = IRLiteral(RustType(RustTypeKind.I64), "0"), ae[0]
                elif len(ae) == 2:
                    se, ee = ae[0], ae[1]
                else:
                    fail("range step", n)
                (
                    fail("int range", n)
                    if not (
                        se.rtype.kind == RustTypeKind.I64
                        and ee.rtype.kind == RustTypeKind.I64
                    )
                    else None
                )
                return IRRangeCtor(RustType(RustTypeKind.RANGE), se, ee, exclusive=True)
            if fname in self.symtab.struct_defs:
                ffa: list[tuple[str, IRExpr]] = []
                sfd = self.symtab.struct_defs[fname]
                fail("Many args", n) if len(ae) > len(sfd) else None
                for i, (fn, ft) in enumerate(sfd.items()):
                    if i < len(ae):
                        ae_i = ae[i]
                        fail("Type bad", n) if not ae_i.rtype.unify(ft) else None
                        ffa.append((fn, ae_i))
                for kw in n.keywords:
                    kwfn = kw.arg
                    fail("No name", n) if not kwfn else None
                    kwe = self.visit_expr(kw.value)
                    eft = sfd.get(kwfn)
                    fail("No field", n) if not eft else None
                    fail("Type bad", n) if not kwe.rtype.unify(eft) else None
                    if any(f[0] == kwfn for f in ffa):
                        fail("Dup field", n)
                    ffa.append((kwfn, kwe))
                return IRStructCtor(
                    RustType(RustTypeKind.STRUCT, name=fname), fname, ffa
                )
            sig = (
                self.symtab.func_sigs.get(fname)
                if hasattr(self.symtab, "func_sigs")
                else None
            )
            if sig:
                eat, ret = sig
                fail("Arg count", n) if len(ae) != len(eat) else None
                for i, (ae_i, et) in enumerate(zip(ae, eat)):
                    fail("Arg type", n) if not ae_i.rtype.unify(et) else None
                return IRCall(ret, fname, ae)
            fail(f"Unknown function {fname}", n)
        fail("Bad call", n)

    def visit_fstr(self, n: ast.JoinedStr) -> IRExpr:
        fsp: list[str] = []
        fa: list[IRExpr] = []
        for p in n.values:
            if isinstance(p, ast.Constant):
                fail("Not str", p) if not isinstance(p.value, str) else None
                fsp.append(p.value.replace("{", "{{").replace("}", "}}"))
            elif isinstance(p, ast.FormattedValue):
                fsp.append("{}")
                fa.append(self.visit_expr(p.value))
            else:
                fail("Bad f-str", p)
        return IRFString(RustType(RustTypeKind.STRING), "".join(fsp), fa)

    def visit_stmt(self, n: ast.stmt) -> list[IRStmt]:
        if isinstance(n, ast.AnnAssign):
            fail("Not name", n.target) if not isinstance(n.target, ast.Name) else None
            nm = n.target.id
            dt = self.parse_anno(n.annotation)
            ie: IRExpr | None = None
            if n.value:
                ie = self.visit_expr(n.value)
                fail("Type bad", n) if not ie.rtype.unify(dt) else None
            self.symtab.declare(nm, dt, is_mut=True)
            return [IRVarDecl(nm, dt, True, ie)]
        if isinstance(n, ast.Assign):
            fail("Mul target", n) if len(n.targets) != 1 else None
            tgt, ve = n.targets[0], self.visit_expr(n.value)
            if isinstance(tgt, ast.Name):
                nm = tgt.id
                sy = self.symtab.lookup(nm)
                if sy:
                    fail("Type bad", n) if not ve.rtype.unify(sy.rtype) else None
                    fail("Immut", n) if not sy.is_mut else None
                    return [IRAssign(nm, ve)]
                else:
                    self.symtab.declare(nm, ve.rtype, is_mut=True)
                    return [IRVarDecl(nm, ve.rtype, True, ve)]
            if isinstance(tgt, ast.Attribute):
                oe = self.visit_expr(tgt.value)
                fn = tgt.attr
                fail("Not struct", n) if oe.rtype.kind != RustTypeKind.STRUCT else None
                ft = self.symtab.get_struct_field_type(oe.rtype.name, fn)
                fail("No field", n) if not ft else None
                fail("Type bad", n) if not ve.rtype.unify(ft) else None
                if isinstance(oe, IRVariable):
                    os = self.symtab.lookup(oe.name)
                    fail("Immut struct", n) if not os or not os.is_mut else None
                if isinstance(oe, IRVariable):
                    oi = oe.name
                else:
                    oi = "self"
                return [IRFieldAssign(oi, fn, ve)]
            fail("Bad assign", n)
        if isinstance(n, ast.Expr):
            return [IRExprStmt(self.visit_expr(n.value))]
        if isinstance(n, ast.If):
            ce = self.visit_expr(n.test)
            fail("Bool cond", n) if ce.rtype.kind != RustTypeKind.BOOL else None
            self.symtab.enter_scope()
            tb = self.visit_block(n.body)
            self.symtab.exit_scope()
            eb: list[IRStmt] | None = None
            if n.orelse:
                self.symtab.enter_scope()
                eb = self.visit_block(n.orelse)
                self.symtab.exit_scope()
            return [IRIf(ce, tb, eb)]
        if isinstance(n, ast.While):
            ce = self.visit_expr(n.test)
            fail("Bool cond", n) if ce.rtype.kind != RustTypeKind.BOOL else None
            self.symtab.enter_scope()
            body = self.visit_block(n.body)
            self.symtab.exit_scope()
            return [IRWhile(ce, body)]
        if isinstance(n, ast.For):
            (
                fail("Complex for", n.target)
                if not isinstance(n.target, ast.Name)
                else None
            )
            tn = n.target.id
            ite = self.visit_expr(n.iter)
            lvt: RustType | None = (
                ite.rtype.inner[0]
                if ite.rtype.kind == RustTypeKind.VEC
                else (
                    RustType(RustTypeKind.I64)
                    if ite.rtype.kind == RustTypeKind.RANGE
                    else fail("Bad iter", n.iter)
                )
            )
            self.symtab.enter_scope()
            self.symtab.declare(tn, lvt, is_mut=False)
            body = self.visit_block(n.body)
            self.symtab.exit_scope()
            return [IRFor(tn, ite, body)]
        if isinstance(n, ast.Return):
            ve: IRExpr | None = None
            if n.value:
                ve = self.visit_expr(n.value)
                (
                    fail("Type bad", n)
                    if not ve.rtype.unify(self.current_func_ret_type)
                    else None
                )
            elif self.current_func_ret_type.kind not in (
                RustTypeKind.UNIT,
                RustTypeKind.VOID,
            ):
                fail("Return bad", n)
            return [IRReturn(ve)]
        if isinstance(n, ast.Pass):
            return []
        fail("Bad stmt", n)

    def visit_block(self, stmts: list[ast.stmt]) -> list[IRStmt]:
        self.symtab.enter_scope()
        ir = []
        [ir.extend(self.visit_stmt(s)) for s in stmts]
        self.symtab.exit_scope()
        return ir

    def scan_decl(self, tree: ast.Module) -> None:
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                flds: dict[str, RustType] = {}
                for it in n.body:
                    if isinstance(it, ast.AnnAssign) and isinstance(
                        it.target, ast.Name
                    ):
                        flds[it.target.id] = self.parse_anno(it.annotation)
                self.symtab.register_struct(n.name, flds)
                for it in n.body:
                    if isinstance(it, ast.FunctionDef):
                        at: list[RustType] = [
                            self.parse_anno(a.annotation)
                            for a in it.args.args
                            if a.arg != "self"
                        ]
                        rt = (
                            self.parse_anno(it.returns)
                            if it.returns
                            else RustType(RustTypeKind.UNIT)
                        )
                        self.symtab.register_method(n.name, it.name, at, rt)
            if isinstance(n, ast.FunctionDef):
                at: list[RustType] = [
                    self.parse_anno(a.annotation) for a in n.args.args
                ]
                rt = (
                    self.parse_anno(n.returns)
                    if n.returns
                    else RustType(RustTypeKind.UNIT)
                )
                self.symtab.func_sigs = getattr(self.symtab, "func_sigs", {}) | {
                    n.name: (at, rt)
                }

    def is_main_guard(self, n: ast.stmt) -> bool:
        """Check if statement is: if __name__ == "__main__": ..."""
        if not isinstance(n, ast.If):
            return False
        cond = n.test
        if isinstance(cond, ast.Compare) and len(cond.ops) == 1:
            left, op, right = cond.left, cond.ops[0], cond.comparators[0]
            if isinstance(op, ast.Eq):
                # Check __name__ == "__main__"
                if (
                    isinstance(left, ast.Name)
                    and left.id == "__name__"
                    and isinstance(right, ast.Constant)
                    and right.value == "__main__"
                ):
                    return True
                # Check "__main__" == __name__
                if (
                    isinstance(right, ast.Name)
                    and right.id == "__name__"
                    and isinstance(left, ast.Constant)
                    and left.value == "__main__"
                ):
                    return True
        return False

    def compile(self, tree: ast.Module) -> IRModule:
        self.scan_decl(tree)
        st: list[IRStructDecl] = []
        fn: list[IRFuncDecl] = []
        mb: list[IRStmt] = []
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                flds = [(nm, t) for nm, t in self.symtab.struct_defs[n.name].items()]
                st.append(IRStructDecl(n.name, flds))
                for it in n.body:
                    if isinstance(it, ast.FunctionDef):
                        sig = self.symtab.get_method_sig(n.name, it.name)
                        self.current_func_ret_type = (
                            sig[1] if sig else RustType(RustTypeKind.UNIT)
                        )
                        self.symtab.enter_scope()
                        ar = []
                        for a in it.args.args:
                            if a.arg == "self":
                                self.symtab.declare(
                                    "self",
                                    RustType(RustTypeKind.STRUCT, name=n.name),
                                    is_mut=True,
                                )
                            else:
                                t = self.parse_anno(a.annotation)
                                self.symtab.declare(a.arg, t, is_mut=False, is_arg=True)
                                ar.append((a.arg, t))
                        bdy = self.visit_block(it.body)
                        self.symtab.exit_scope()
                        fn.append(
                            IRFuncDecl(
                                it.name,
                                ar,
                                self.current_func_ret_type,
                                bdy,
                                is_method=True,
                                self_type=RustType(RustTypeKind.STRUCT, name=n.name),
                            )
                        )
            elif isinstance(n, ast.FunctionDef):
                sig = (
                    self.symtab.func_sigs.get(n.name)
                    if hasattr(self.symtab, "func_sigs")
                    else None
                )
                self.current_func_ret_type = (
                    sig[1] if sig else RustType(RustTypeKind.UNIT)
                )
                self.symtab.enter_scope()
                ar = []
                for a in n.args.args:
                    t = self.parse_anno(a.annotation)
                    self.symtab.declare(a.arg, t, is_mut=False, is_arg=True)
                    ar.append((a.arg, t))
                bdy = self.visit_block(n.body)
                self.symtab.exit_scope()
                fn.append(IRFuncDecl(n.name, ar, self.current_func_ret_type, bdy))
            elif self.is_main_guard(n):
                # Handle if __name__ == "__main__": by extracting body
                if_node = cast(ast.If, n)
                mb.extend(self.visit_block(if_node.body))
            else:
                mb.extend(self.visit_stmt(n))
        return IRModule(st, fn, mb)


class Emitter:
    def __init__(self):
        self.ind = 0
        self.has_input = False  # Track if we need input helpers

    def i(self) -> str:
        return "    " * self.ind

    def e_type(self, t: RustType) -> str:
        return str(t)

    def e_expr(self, e: IRExpr) -> str:
        if isinstance(e, IRLiteral):
            if e.rtype.kind == RustTypeKind.STRING:
                # Detect if this literal is a prompt for __read_input
                if getattr(e, "is_input_prompt", False):
                    return e.value  # Emit as &str literal directly
                return f"String::from({e.value})"
            return e.value
        if isinstance(e, IRVariable):
            return e.name
        if isinstance(e, IRBinaryOp):
            return f"{self.e_expr(e.left)} {e.op} {self.e_expr(e.right)}"
        if isinstance(e, IRUnaryOp):
            return f"{e.op}{self.e_expr(e.operand)}"
        if isinstance(e, IRCall):
            ae = [self.e_expr(a) for a in e.args]
            ja = ", ".join(ae)
            if e.func_name == "println!":
                return f'println!("{{}}", {ja})' if ae else "println!()"
            if e.is_method and e.instance:
                # Special handling for parse method - no arguments, with unwrap
                if e.func_name == "parse":
                    return f"{self.e_expr(e.instance)}.parse::<i64>().unwrap_or(0)"
                return f"{self.e_expr(e.instance)}.{e.func_name}({ja})"
            return f"{e.func_name}({ja})"
        if isinstance(e, IRListCtor):
            return f"vec![{', '.join([self.e_expr(x) for x in e.elements])}]"
        if isinstance(e, IRDictCtor):
            ps = [
                f"({self.e_expr(k)}, {self.e_expr(v)})"
                for k, v in zip(e.keys, e.values)
            ]
            return f"std::collections::HashMap::from([{', '.join(ps)}])"
        if isinstance(e, IRStructCtor):
            fs = [f"{n}: {self.e_expr(v)}" for n, v in e.fields]
            return (
                f"{e.struct_name} {{ {', '.join(fs)} }}"
                if fs
                else f"{e.struct_name}::default()"
            )
        if isinstance(e, IRFieldAccess):
            return f"{self.e_expr(e.instance)}.{e.field}"
        if isinstance(e, IRRangeCtor):
            return f"{self.e_expr(e.start)}..{self.e_expr(e.end)}"
        if isinstance(e, IRFString):
            fa = [self.e_expr(a) for a in e.args]
            return (
                f'format!("{e.fmt_str}", {", ".join(fa)})'
                if fa
                else f'"{e.fmt_str}".to_string()'
            )
        if isinstance(e, IRIndexAccess):
            return f"{self.e_expr(e.container)}[{self.e_expr(e.index)}]"
        return "/*unknown*/"

    def e_stmt(self, s: IRStmt, in_init: bool = False) -> str:
        ii = self.i()
        if isinstance(s, IRVarDecl):
            m = "mut " if s.is_mut else ""
            ie = f" = {self.e_expr(s.init)}" if s.init else ""
            return f"{ii}let {m}{s.name}: {self.e_type(s.rtype)}{ie};\n"
        if isinstance(s, IRAssign):
            return f"{ii}{s.target} = {self.e_expr(s.value)};\n"
        if isinstance(s, IRFieldAssign):
            # Skip field assignments in __init__ - they'll be in the struct literal
            if in_init:
                return ""
            return f"{ii}{s.obj_name}.{s.field_name} = {self.e_expr(s.value)};\n"
        if isinstance(s, IRExprStmt):
            return f"{ii}{self.e_expr(s.expr)};\n"
        if isinstance(s, IRReturn):
            rv = f" {self.e_expr(s.value)}" if s.value else ""
            return f"{ii}return{rv};\n"
        if isinstance(s, IRIf):
            r = f"{ii}if {self.e_expr(s.condition)} {{\n"
            self.ind += 1
            for st in s.then_block:
                r += self.e_stmt(st, in_init=in_init)
            self.ind -= 1
            if s.else_block:
                r += f"{ii}}} else {{\n"
                self.ind += 1
                for st in s.else_block:
                    r += self.e_stmt(st, in_init=in_init)
                self.ind -= 1
                r += f"{ii}}}\n"
            else:
                r += f"{ii}}}\n"
            return r
        if isinstance(s, IRWhile):
            r = f"{ii}while {self.e_expr(s.condition)} {{\n"
            self.ind += 1
            for st in s.body:
                r += self.e_stmt(st)
            self.ind -= 1
            r += f"{ii}}}\n"
            return r
        if isinstance(s, IRFor):
            iie = self.e_expr(s.iterator)
            iie = (
                f"&{iie}"
                if isinstance(s.iterator, IRVariable)
                and s.iterator.rtype.kind == RustTypeKind.VEC
                else iie
            )
            r = f"{ii}for {s.target_name} in {iie} {{\n"
            self.ind += 1
            for st in s.body:
                r += self.e_stmt(st)
            self.ind -= 1
            r += f"{ii}}}\n"
            return r
        return f"{ii}/*unknown stmt*/\n"

    def emit(self, m: IRModule) -> str:
        # Start with helper functions and imports
        o = "#[allow(unused_imports)]\n"
        o += "use std::collections::HashMap;\n"
        o += "use std::io::{self, Write};\n\n"

        # Add input helper function
        o += "fn __read_input(prompt: &str) -> String {\n"
        o += '    print!("{}", prompt);\n'
        o += "    io::stdout().flush().ok();\n"
        o += "    let mut input = String::new();\n"
        o += "    io::stdin().read_line(&mut input).ok();\n"
        o += "    input.trim().to_string()\n"
        o += "}\n\n"
        for s in m.structs:
            o += f"#[derive(Debug, Clone)]\nstruct {s.name} {{\n"
            for fn, ft in s.fields:
                o += f"    pub {fn}: {self.e_type(ft)},\n"
            o += "}\n\n"
        mtd: dict[str, list[IRFuncDecl]] = {}
        for f in m.funcs:
            if f.is_method and f.self_type:
                sn = f.self_type.name
                if sn not in mtd:
                    mtd[sn] = []
                mtd[sn].append(f)
            else:
                ret_sig = (
                    f"-> {self.e_type(f.ret_type)}"
                    if f.ret_type.kind not in (RustTypeKind.VOID, RustTypeKind.UNIT)
                    else ""
                )
                o += f"fn {f.name}({', '.join([f'{n}: {self.e_type(t)}' for n,t in f.args])}) {ret_sig} {{\n"
                self.ind += 1
                for st in f.body:
                    o += self.e_stmt(st)
                self.ind -= 1
                o += "}\n\n"
        for sn, fs in mtd.items():
            o += f"impl {sn} {{\n"
            self.ind += 1
            for f in fs:
                ar = ", ".join([f"{n}: {self.e_type(t)}" for n, t in f.args])
                ret_type = "Self" if f.name == "__init__" else self.e_type(f.ret_type)
                ret_sig = (
                    f" -> {ret_type}"
                    if f.name == "__init__"
                    or f.ret_type.kind not in (RustTypeKind.VOID, RustTypeKind.UNIT)
                    else ""
                )
                fn_name = "new" if f.name == "__init__" else f.name
                # __init__ (new) doesn't need &mut self, other methods do
                self_param = "" if f.name == "__init__" else "&mut self"
                o += f"    pub fn {fn_name}({self_param}{f', {ar}' if ar and self_param else f'{ar}' if ar else ''}){ret_sig} {{\n"
                self.ind += 1
                for st in f.body:
                    in_init = f.name == "__init__"
                    # In __init__, skip field assignments - they'll be in struct init
                    if in_init and isinstance(st, IRFieldAssign):
                        continue
                    o += self.e_stmt(st, in_init=in_init)
                # Add implicit return self for __init__ methods
                if f.name == "__init__":
                    # Emit Self { field: value, ... }
                    # Extract field->value mappings from the body
                    field_values = {}
                    for st in f.body:
                        if isinstance(st, IRFieldAssign):
                            # This will be skipped in e_stmt, but we capture it here
                            if st.obj_name == "self":
                                # Get the parameter value
                                if isinstance(st.value, IRVariable):
                                    field_values[st.field_name] = st.value.name
                                else:
                                    # For complex expressions, we'd need to emit them
                                    field_values[st.field_name] = self.e_expr(st.value)

                    o += f"{self.i()}Self {{\n"
                    self.ind += 1
                    # Get struct field names from the module's structs
                    if f.self_type:
                        struct_name = f.self_type.name
                        # Find the struct in the module
                        struct_decl = None
                        for s in m.structs:
                            if s.name == struct_name:
                                struct_decl = s
                                break
                        if struct_decl:
                            # Use the actual field-value mappings from assignments
                            for field_name, _ in struct_decl.fields:
                                if field_name in field_values:
                                    o += f"{self.i()}{field_name}: {field_values[field_name]},\n"
                                else:
                                    # If no assignment, use the param with same name
                                    o += f"{self.i()}{field_name}: {field_name},\n"
                    self.ind -= 1
                    o += f"{self.i()}}}\n"
                self.ind -= 1
                o += "    }\n"
            self.ind -= 1
            o += "}\n\n"
        o += "fn main() {\n"
        self.ind += 1
        for s in m.main_block:
            o += self.e_stmt(s)
        self.ind -= 1
        o += "}\n"
        return o


try:
    import typer

    has_typer = True
except ImportError:
    has_typer = False


def compile_and_run(out_path: str, run: bool):
    # delete any old PDBs in the same folder
    for pdb_file in glob.glob(os.path.join(os.path.dirname(out_path), "*.pdb")):
        try:
            os.remove(pdb_file)
        except Exception:
            pass

    bin_out = out_path.replace(".rs", "")
    if os.name == "nt":
        bin_out += ".exe"

    rust_flags = ["-C", "link-arg=/DEBUG:NONE"] if os.name == "nt" else []

    print("[✓] Compiling with rustc...", file=sys.stderr)
    try:
        r = subprocess.run(
            ["rustc", out_path, "-o", bin_out] + rust_flags,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            print(f"[✗] Compilation failed:\n{r.stderr}", file=sys.stderr)
            sys.exit(1)
        print(f"[✓] Compiled: {bin_out}", file=sys.stderr)
        if run:
            print(f"[▶] Running {bin_out}...", file=sys.stderr)
            subprocess.run([bin_out], check=True)
    except subprocess.TimeoutExpired:
        print("[✗] Compilation timeout", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"[✗] Could not run rustc or the binary: {e}", file=sys.stderr)
        print(
            "[💡] Make sure Rust is installed and rustc is in PATH: https://rustup.rs/",
            file=sys.stderr,
        )
        sys.exit(1)


if has_typer:
    app = typer.Typer()

    @app.command()
    def main(
        inp: str = typer.Argument(..., help="Input Python file"),
        out: str = typer.Option(None, "--output", "-o", help="Output Rust file"),
        no_comp: bool = typer.Option(False, "--no-compile", help="Skip compilation"),
        run: bool = typer.Option(False, "--run", help="Execute after compile"),
    ):
        try:
            with open(inp) as f:
                src = f.read()
        except Exception as e:
            typer.echo(f"Error reading {inp}: {e}", err=True)
            raise typer.Exit(1)

        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            typer.echo(f"Syntax error: {e}", err=True)
            raise typer.Exit(1)

        c = Compiler()
        ir = c.compile(tree)
        em = Emitter()
        rust = em.emit(ir)

        if out is None:
            out = inp.replace(".py", ".rs")

        with open(out, "w") as f:
            f.write(rust)
        typer.echo(f"[✓] Generated {out}", err=True)

        if not no_comp:
            compile_and_run(out, run)
        else:
            typer.echo("[⊘] Compilation skipped", err=True)

    if __name__ == "__main__":
        app()

else:
    if __name__ == "__main__":
        if len(sys.argv) < 2:
            print("Usage: p2r.py <input.py> [output.rs]", file=sys.stderr)
            sys.exit(1)

        inp = sys.argv[1]
        out = sys.argv[2] if len(sys.argv) > 2 else inp.replace(".py", ".rs")

        try:
            with open(inp) as f:
                src = f.read()
        except Exception as e:
            print(f"Error reading {inp}: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            print(f"Syntax error: {e}", file=sys.stderr)
            sys.exit(1)

        c = Compiler()
        ir = c.compile(tree)
        em = Emitter()
        rust = em.emit(ir)

        with open(out, "w") as f:
            f.write(rust)
        print(f"✓ Generated {out}", file=sys.stderr)

        compile_and_run(out, run=True)
