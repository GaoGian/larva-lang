#coding=utf8

"""
输出为go代码
"""

import os
import shutil
import sys
import platform
import subprocess
import re

import larc_common
import larc_module
import larc_token
import larc_expr
import larc_type

_POS_INFO_IGNORE = object()

main_module_name = None
out_dir = None
runtime_dir = None

_out_prog_dir = None
_prog_module_name = None

_prog_name = None
_exe_file = None

_tb_map = {}

class _Code:
    class _CodeBlk:
        def __init__(self, code, end_line):
            self.code = code
            self.end_line = end_line

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                return
            assert len(self.code.indent) >= 4
            self.code.indent = self.code.indent[: -4]
            self.code += self.end_line

    class _NativeCode:
        def __init__(self, code):
            self.code = code

        def __enter__(self):
            self.code += "//native_code start"
            self.save_indent = self.code.indent
            self.code.indent = ""
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                return
            self.code.indent = self.save_indent
            self.code += "//native_code end"

    def __init__(self, file_path_name, pkg_name = None):
        assert file_path_name.endswith(".go")
        self.file_path_name_base = file_path_name[: -3]
        self.line_list_map = {}
        self.pkg_name = _prog_module_name if pkg_name is None else pkg_name
        self.indent = ""

        self.file_path_name = None
        self.line_list = None
        self.switch_file("")

    def switch_file(self, file_name):
        assert self.indent == ""
        if file_name != "":
            file_name = "." + file_name
        self.file_path_name = self.file_path_name_base + file_name + ".go"
        if file_name in self.line_list_map:
            self.line_list = self.line_list_map[file_name]
        else:
            self.line_list = self.line_list_map[file_name] = []
            self += "package %s" % self.pkg_name

    def __iadd__(self, line):
        self.line_list.append(self.indent + line)
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            return
        for file_name, line_list in self.line_list_map.iteritems():
            f = open(self.file_path_name_base + file_name + ".go", "w")
            for line in line_list:
                print >> f, line
            f.close()

    def new_blk(self, title, start_with_blank_line = True):
        if start_with_blank_line:
            self += ""
        end_line = "}"
        if title == "import":
            self += title + " ("
            end_line = ")"
        elif title == "else" or title.startswith("else if "):
            if start_with_blank_line:
                del self.line_list[-1]
            assert self.line_list[-1] == self.indent + "}"
            del self.line_list[-1]
            self += "} " + title + " {"
        elif title:
            self += title + " {"
        else:
            self += "{"
        self.indent += " " * 4
        return self._CodeBlk(self, end_line)

    def new_native_code(self):
        return self._NativeCode(self)

    #记录tb映射的信息，本code当前位置到输入的pos信息，在输出代码之前使用，adjust为代码行数修正值
    def record_tb_info(self, pos_info, adjust = 0):
        if pos_info is _POS_INFO_IGNORE:
            tb_info = None
        else:
            t, fom = pos_info
            if fom is None:
                fom = "<module>"
            tb_info = t.src_file, t.line_no, str(fom)
        _tb_map[(self.file_path_name, len(self.line_list) + 1 + adjust)] = tb_info

#gen funcs -----------------------------------------------------------------------------------------------

def _gen_module_name_code(module):
    #模块名的代码，分两种情况
    if "/" not in module.name:
        #单模块名直接用长度+名字
        return "%d_%s" % (len(module.name), module.name)
    #多级模块名需要增加级数
    pl = module.name.split("/")
    return "%d_%s" % (len(pl), "_".join(["%d_%s" % (len(p), p) for p in pl]))

def _gen_coi_name(coi):
    if coi.is_closure:
        return "lar_%s" % coi.name

    for i in "cls", "gcls_inst", "intf", "gintf_inst":
        if eval("coi.is_" + i):
            coi_name = "lar_" + i
            break
    else:
        raise Exception("Bug")
    coi_name += "_%s_%d_%s" % (_gen_module_name_code(coi.module), len(coi.name), coi.name)
    if coi.is_gcls_inst or coi.is_gintf_inst:
        #泛型实例还需增加泛型参数信息
        coi_name += "_%d" % len(coi.gtp_map)
        for tp in coi.gtp_map.itervalues():
            coi_name += "_%s" % _gen_type_name_code_without_star(tp)
    return coi_name

_BASE_TYPE_NAME_MAP = {"bool": "bool",
                       "schar": "int8", "char": "uint8",
                       "short": "int16", "ushort": "uint16",
                       "int": "int32", "uint": "uint32",
                       "long": "int64", "ulong": "uint64",
                       "float": "float32", "double": "float64"}

def _gen_non_array_type_name(tp):
    assert not (tp.is_array or tp.is_nil or tp.is_void or tp.is_literal_int)
    if tp.is_obj_type:
        return _gen_coi_name(tp.get_coi())
    assert tp.token.is_reserved
    return _BASE_TYPE_NAME_MAP[tp.name]

def _gen_arr_tp_name(elem_tp_name, dim_count):
    return "lar_arr_%s_%d" % (elem_tp_name, dim_count)

def _gen_arr_tp_name_code(elem_tp_name, dim_count):
    return "*" + _gen_arr_tp_name(elem_tp_name, dim_count)

def _gen_type_name_code(tp):
    if tp.is_void:
        return ""
    array_dim_count = tp.array_dim_count
    while tp.is_array:
        tp = tp.to_elem_type()
    tp_name = _gen_non_array_type_name(tp)
    if array_dim_count > 0:
        return _gen_arr_tp_name_code(tp_name, array_dim_count)
    return _gen_tp_name_code_from_tp_name(tp_name)

def _gen_type_name_code_without_star(tp):
    c = _gen_type_name_code(tp)
    if c[0] == "*":
        c = c[1 :]
    assert c and re.match("^\w*$", c) is not None
    return c

def _gen_tp_name_code_from_tp_name(tp_name):
    if tp_name.startswith("lar_cls") or tp_name.startswith("lar_gcls") or tp_name.startswith("lar_closure"):
        return "*" + tp_name
    return tp_name

def _gen_new_arr_func_name_by_tp_name(tp_name, dim_count, new_dim_count):
    assert dim_count >= new_dim_count > 0
    return "lar_util_new_arr_%s_%d_%d" % (tp_name, dim_count, new_dim_count)

def _gen_new_arr_func_name(tp, dim_count, new_dim_count):
    assert not tp.is_array and dim_count >= new_dim_count > 0
    return _gen_new_arr_func_name_by_tp_name(_gen_non_array_type_name(tp), dim_count, new_dim_count)

def _gen_func_name(func):
    for i in "func", "gfunc_inst":
        if eval("func.is_" + i):
            func_name = "lar_" + i
            break
    else:
        raise Exception("Bug")
    func_name += "_%s_%d_%s" % (_gen_module_name_code(func.module), len(func.name), func.name)
    if func.is_gfunc_inst:
        #泛型实例还需增加泛型参数信息
        func_name += "_%d" % len(func.gtp_map)
        for tp in func.gtp_map.itervalues():
            func_name += "_%s" % _gen_type_name_code_without_star(tp)
    return func_name

def _gen_init_mod_func_name(module):
    return "lar_env_init_mod_" + _gen_module_name_code(module)

def _gen_str_literal_name(t):
    return "lar_literal_str_%s_%d" % (_gen_module_name_code(_curr_module), t.id)

def _gen_number_literal_name(t):
    return "lar_literal_number_%s_%d" % (_gen_module_name_code(_curr_module), t.id)

def _gen_str_literal(s):
    code_list = []
    for c in s:
        asc = ord(c)
        assert 0 <= asc <= 0xFF
        if asc < 32 or asc > 126 or c in ('"', "\\"):
            code_list.append("\\%03o" % asc)
        else:
            code_list.append(c)
    return '"%s"' % "".join(code_list)

def _gen_gv_name(gv):
    return "lar_gv_%s_%d_%s" % (_gen_module_name_code(gv.module), len(gv.name), gv.name)

def _gen_default_value_code(tp):
    if tp.is_void:
        return ""
    if tp.is_bool_type:
        return "false"
    if tp.is_number_type:
        return "0"
    assert tp.is_obj_type
    return "nil"

def _gen_method_name_code(method):
    #public的用同样的名字模板，非public的要加上module名，限定在模块内部使用
    if "public" in method.decr_set:
        return "lar_method_" + method.name
    return "lar_method_%s_%d_%s" % (_gen_module_name_code(method.module), len(method.name), method.name)

def _gen_se_expr_code(expr):
    if expr.expr is None:
        assert expr.op in ("++", "--")
        return "%s %s" % (_gen_expr_code(expr.lvalue), expr.op)
    return "%s %s %s" % (_gen_expr_code(expr.lvalue), expr.op, _gen_expr_code(expr.expr))

def _gen_expr_list_code(expr_list):
    return ", ".join([_gen_expr_code(e) for e in expr_list])

def _gen_expr_code(expr):
    if expr.is_se_expr:
        return _gen_se_expr_code(expr)
    assert expr.is_expr
    expr_code = _gen_expr_code_ex(expr)
    if expr.is_ref:
        expr_code = "&(%s)" % expr_code
    return expr_code

def _gen_expr_code_ex(expr):
    if expr.op == "force_convert":
        tp, e = expr.arg
        tp_name_code = _gen_type_name_code(tp)
        e_code = _gen_expr_code(e)
        if e.type.is_coi_type and e.type.get_coi().is_intf_any():
            #Any到其他类型的转换，用断言
            return "(%s).(%s)" % (e_code, tp_name_code)
        if tp.can_convert_from(e.type) or not tp.is_obj_type:
            #可以隐式转换，或到基础类型的强转，用go的强转形式（Any到基础类型的强转已经在上面处理了）
            return "(%s)(%s)" % (tp_name_code, e_code)
        #接口到其他类型的断言
        assert e.type.is_coi_type
        e_coi = e.type.get_coi()
        assert e_coi.is_intf or e_coi.is_gintf_inst
        return "(%s).(%s)" % (e_code, tp_name_code)

    if expr.op in ("~", "!", "neg", "pos"):
        e = expr.arg
        return "%s(%s)" % ({"~" : "^", "!" : "!", "neg" : "-", "pos" : "+"}[expr.op], _gen_expr_code(e))

    if expr.op in larc_token.BINOCULAR_OP_SYM_SET:
        ea, eb = expr.arg
        ea_code = _gen_expr_code(ea)
        eb_code = _gen_expr_code(eb)
        if expr.op == "%" and ea.type.is_float_type:
            assert eb.type.is_float_type
            return "lar_util_fmod_%s((%s), (%s))" % (ea.type.name, ea_code, eb_code)
        return "(%s) %s (%s)" % (ea_code, expr.op, eb_code)

    if expr.op == "local_var":
        name = expr.arg
        return "%sl_%s" % ("*" if expr.type.is_ref else "", name)

    if expr.op == "literal":
        t = expr.arg
        assert t.type.startswith("literal_")
        literal_type = t.type[8 :]
        tp = expr.type
        if literal_type in ("nil", "bool"):
            return t.value
        assert t.id in _literal_token_id_set
        if literal_type == "str":
            return _gen_str_literal_name(t)
        return _gen_number_literal_name(t)

    if expr.op == "new":
        expr_list = expr.arg
        tp = expr.type
        return "lar_new_obj_%s(%s)" % (_gen_non_array_type_name(tp), _gen_expr_list_code(expr_list))

    if expr.op == "new_obj_init_by_attr":
        attr_init_map = expr.arg
        tp = expr.type
        init_code_list = []
        for name, e in attr_init_map.iteritems():
            init_code_list.append("m_%s: (%s)" % (name, _gen_expr_code(e)))
        return "&%s{%s}" % (_gen_non_array_type_name(tp), ", ".join(init_code_list))

    if expr.op == "default_value":
        tp = expr.arg
        assert tp == expr.type
        return "(%s)(%s)" % (_gen_type_name_code(tp), _gen_default_value_code(tp))

    if expr.op == "new_array":
        tp, size_list = expr.arg
        try:
            new_dim_count = size_list.index(None)
        except ValueError:
            new_dim_count = len(size_list)
        assert new_dim_count > 0
        new_arr_method = larc_type.gen_arr_type(tp.to_array_type(len(size_list))).get_coi().method_map["new_arr"]
        return ("((%s)(nil)).%s([]int64{%s})" %
                (_gen_arr_tp_name_code(_gen_non_array_type_name(tp), len(size_list)), _gen_method_name_code(new_arr_method),
                 ", ".join(["int64(%s)" % _gen_expr_code(e) for e in size_list[: new_dim_count]])))

    if expr.op == "new_array_by_init_list":
        expr_list = expr.arg
        expr_code_list = []
        for e in expr_list:
            if isinstance(e, tuple):
                first_e, second_e = e
                expr_code = "{(%s), (%s)}" % (_gen_expr_code(first_e), _gen_expr_code(second_e))
            else:
                expr_code = "(%s)" % _gen_expr_code(e)
            expr_code_list.append(expr_code)
        array_type = expr.type
        assert array_type.is_array
        elem_type = array_type.to_elem_type()
        return "&%s{arr: []%s{%s}}" % (_gen_type_name_code(array_type)[1 :], _gen_type_name_code(elem_type), ", ".join(expr_code_list))

    if expr.op == "this":
        return "this"

    if expr.op == "[]":
        arr_e, e = expr.arg
        return "(%s).arr[%s]" % (_gen_expr_code(arr_e), _gen_expr_code(e))

    if expr.op == "[:]":
        arr_e, ea, eb = expr.arg
        arr_tp_name_code = _gen_type_name_code(arr_e.type)
        assert arr_tp_name_code.startswith("*lar_arr_")
        return ("&%s{arr: (%s).arr[%s:%s]}" %
                (arr_tp_name_code[1 :], _gen_expr_code(arr_e), "" if ea is None else _gen_expr_code(ea),
                 "" if eb is None else _gen_expr_code(eb)))

    if expr.op == "call_array.method":
        e, method, expr_list = expr.arg
        assert e.type.is_array
        return "(%s).%s(%s)" % (_gen_expr_code(e), _gen_method_name_code(method), _gen_expr_list_code(expr_list))

    if expr.op == "str_format":
        fmt, expr_list = expr.arg
        return "lar_str_fmt(%s%s%s)" % (_gen_str_literal(fmt), ", " if expr_list else "", _gen_expr_list_code(expr_list))

    if expr.op == "to_go_str":
        e = expr.arg
        if e.type == larc_type.STR_TYPE:
            #针对String小优化一下
            return "lar_str_to_go_str(%s)" % _gen_expr_code(e)
        return "lar_go_func_any_to_go_str(%s)" % _gen_expr_code(e)

    if expr.op == "repr_to_go_str":
        e = expr.arg
        return "lar_go_func_any_repr_to_go_str(%s)" % _gen_expr_code(e)

    if expr.op == "type_name_to_go_str":
        e = expr.arg
        return "lar_go_func_any_type_name_to_go_str(%s)" % _gen_expr_code(e)

    if expr.op == "call_method":
        e, method, expr_list = expr.arg
        return "(%s).%s(%s)" % (_gen_expr_code(e), _gen_method_name_code(method), _gen_expr_list_code(expr_list))

    if expr.op == ".":
        e, attr = expr.arg
        return "(%s).m_%s" % (_gen_expr_code(e), attr.name)

    if expr.op == "global_var":
        gv = expr.arg
        return _gen_gv_name(gv)

    if expr.op == "call_func":
        func, expr_list = expr.arg
        if func.module.name == "__builtins" and func.name in ("catch_base", "catch"):
            assert not expr_list
            expr_list_code = "recover()"
        else:
            expr_list_code = _gen_expr_list_code(expr_list)
        return "%s(%s)" % (_gen_func_name(func), expr_list_code)

    if expr.op == "closure":
        closure = expr.arg
        coi_name = _gen_coi_name(closure)
        return "&%s{%s}" % (coi_name, ", ".join(["cm_%s: %s_method_%s" % (method.name, coi_name, method.name)
                                                 for method in closure.method_map.itervalues()]))

    raise Exception("Bug")

def _gen_arg_def(arg_map):
    return ", ".join(["l_%s %s%s" % (name, "*" if tp.is_ref else "", _gen_type_name_code(tp)) for name, tp in arg_map.iteritems()])

#gen funcs end -----------------------------------------------------------------------------------------------

_main_pkg_file = None

_BOOTER_START_PROC_FUNC_NAME = "Lar_booter_start_prog"

def _output_main_pkg():
    with _Code(_main_pkg_file, "main") as code:
        with code.new_blk("import"):
            code += '"%s"' % _prog_module_name
        with code.new_blk("func main()"):
            code += "%s.%s()" % (_prog_module_name, _BOOTER_START_PROC_FUNC_NAME)

def _output_booter():
    booter_fix_file_path_name = runtime_dir + "/lar_booter.go"
    if not os.path.exists(booter_fix_file_path_name):
        larc_common.exit("runtime文件缺失，请检查编译器环境：[%s]" % booter_fix_file_path_name)
    f = open("%s/%s.booter_fix.go" % (_out_prog_dir, _prog_module_name), "w")
    print >> f, "package %s" % _prog_module_name
    print >> f
    f.write(larc_common.open_src_file(booter_fix_file_path_name).read())
    f.close()

    with _Code("%s/%s.booter.go" % (_out_prog_dir, _prog_module_name)) as code:
        with code.new_blk("func %s()" % _BOOTER_START_PROC_FUNC_NAME):
            code += ("lar_booter_start_prog(%s, %s)" %
                     (_gen_init_mod_func_name(larc_module.module_map[main_module_name]),
                      _gen_func_name(larc_module.module_map[main_module_name].get_main_func())))

def _output_native_code(code, native_code, fom):
    class FakeToken:
        def __init__(self, line_no):
            self.src_file = os.path.join(native_code.module.dir, native_code.file_name)
            self.line_no = native_code.t.line_no + line_no

    with code.new_native_code():
        for line_idx, line in enumerate(native_code.line_list):
            s = ""
            for i in line:
                if isinstance(i, str):
                    s += i
                elif larc_type.is_type(i):
                    s += _gen_type_name_code(i)
                else:
                    assert isinstance(i, tuple)
                    module_name, name = i
                    s += "%s_%d_%s" % (_gen_module_name_code(larc_module.module_map[module_name]), len(name), name)
            code.record_tb_info((FakeToken(line_idx + 1), fom))
            code += s

def _output_closure_method(code, closure):
    coi_name = _gen_coi_name(closure)
    for method in closure.method_map.itervalues():
        with code.new_blk("var %s_method_%s = func (%s) %s" %
                          (coi_name, method.name, _gen_arg_def(method.arg_map), _gen_type_name_code(method.type))):
            _output_stmt_list(code, method.stmt_list)
            code += "return %s" % _gen_default_value_code(method.type)

def _output_stmt_list(code, stmt_list):
    for stmt in stmt_list:
        if stmt.type == "block":
            with code.new_blk(""):
                _output_stmt_list(code, stmt.stmt_list)
            continue

        if stmt.type in ("break", "continue"):
            code += stmt.type
            continue

        if stmt.type == "return":
            #顶层，普通return
            if stmt.expr is not None:
                code.record_tb_info(stmt.expr.pos_info)
            code += "return %s" % ("" if stmt.expr is None else "(%s)" % _gen_expr_code(stmt.expr))
            continue

        if stmt.type == "for":
            with code.new_blk(""):
                if len(stmt.for_var_map) == 0:
                    for expr in stmt.init_expr_list:
                        code.record_tb_info(expr.pos_info)
                        code += _gen_expr_code(expr)
                else:
                    assert len(stmt.for_var_map) == len(stmt.init_expr_list)
                    pos = 0
                    for (name, tp), expr in zip(stmt.for_var_map.iteritems(), stmt.init_expr_list):
                        if pos in stmt.closure_def_map:
                            for closure in stmt.closure_def_map[pos]:
                                _output_closure_method(code, closure)
                        if expr is not None:
                            code.record_tb_info(expr.pos_info)
                        code += "var l_%s %s = (%s)" % (name, _gen_type_name_code(tp),
                                                        _gen_default_value_code(tp) if expr is None else _gen_expr_code(expr))
                        code += "_ = l_%s" % name
                        pos += 1
                    else:
                        if pos in stmt.closure_def_map:
                            for closure in stmt.closure_def_map[pos]:
                                _output_closure_method(code, closure)
                if stmt.judge_expr is None:
                    code += "for ; ;"
                else:
                    code.record_tb_info(stmt.judge_expr.pos_info)
                    code += "for ; %s;" % _gen_expr_code(stmt.judge_expr)
                if len(stmt.loop_expr_list) == 0:
                    blk_title = ""
                elif len(stmt.loop_expr_list) == 1:
                    code.record_tb_info(stmt.loop_expr_list[0].pos_info)
                    blk_title = _gen_expr_code(stmt.loop_expr_list[0])
                else:
                    with code.new_blk("func ()"):
                        for e in stmt.loop_expr_list:
                            code.record_tb_info(e.pos_info)
                            code += _gen_expr_code(e)
                    assert code.line_list[-1].strip() == "}"
                    del code.line_list[-1]
                    blk_title = "}()"
                    code.record_tb_info(_POS_INFO_IGNORE)

                with code.new_blk(blk_title, start_with_blank_line = False):
                    _output_stmt_list(code, stmt.stmt_list)
            continue

        if stmt.type == "foreach":
            with code.new_blk(""):
                code += "var l_%s %s = (%s)" % (stmt.var_name, _gen_type_name_code(stmt.var_tp), _gen_default_value_code(stmt.var_tp))
                code += "_ = l_%s" % (stmt.var_name)
                code.record_tb_info(stmt.iter_expr.pos_info)
                with code.new_blk("for foreach_iter := (%s); !foreach_iter.lar_method_after_end(); foreach_iter.lar_method_inc()" %
                                  _gen_expr_code(stmt.iter_expr), start_with_blank_line = False):
                    code += "l_%s = foreach_iter.lar_method_get()" % (stmt.var_name)
                    _output_stmt_list(code, stmt.stmt_list)
            continue

        if stmt.type == "while":
            code.record_tb_info(stmt.expr.pos_info, adjust = 1)
            with code.new_blk("for (%s)" % _gen_expr_code(stmt.expr)):
                _output_stmt_list(code, stmt.stmt_list)
            continue

        if stmt.type == "if":
            assert len(stmt.if_expr_list) == len(stmt.if_stmt_list_list)
            for i, (if_expr, if_stmt_list) in enumerate(zip(stmt.if_expr_list, stmt.if_stmt_list_list)):
                code.record_tb_info(if_expr.pos_info, adjust = 1 if i == 0 else -1)
                with code.new_blk("%sif (%s)" % ("" if i == 0 else "else ", _gen_expr_code(if_expr))):
                    _output_stmt_list(code, if_stmt_list)
            if stmt.else_stmt_list is not None:
                with code.new_blk("else"):
                    _output_stmt_list(code, stmt.else_stmt_list)
            continue

        if stmt.type == "var":
            if stmt.expr is None:
                expr_code = _gen_default_value_code(stmt_list.var_map[stmt.name])
            else:
                expr_code = _gen_expr_code(stmt.expr)
                code.record_tb_info(stmt.expr.pos_info)
            code += "var l_%s %s = (%s)" % (stmt.name, _gen_type_name_code(stmt_list.var_map[stmt.name]), expr_code)
            code += "_ = l_%s" % stmt.name
            continue

        if stmt.type == "expr":
            code.record_tb_info(stmt.expr.pos_info)
            code += _gen_expr_code(stmt.expr)
            continue

        if stmt.type == "defer_block":
            with code.new_blk("defer func ()"):
                _output_stmt_list(code, stmt.stmt_list)
            assert code.line_list[-1].strip() == "}"
            code.line_list[-1] += "()"
            continue

        if stmt.type == "defer_expr":
            code.record_tb_info(stmt.expr.pos_info)
            code += "defer " + _gen_expr_code(stmt.expr)
            continue

        if stmt.type == "native_code":
            _output_native_code(code, stmt.native_code, stmt.fom)
            continue

        if stmt.type == "def_closure_method":
            _output_closure_method(code, stmt.closure)
            continue

        raise Exception("Bug")

_literal_token_id_set = set()


_curr_module = None
def _output_module():
    module = _curr_module
    module_file_name = "%s/%s.mod.%s.mod.go" % (_out_prog_dir, _prog_module_name, _gen_module_name_code(module))
    with _Code(module_file_name) as code:
        code += ""
        for t in module.literal_str_list:
            assert t.is_literal("str") and t.id not in _literal_token_id_set
            _literal_token_id_set.add(t.id)
            code += ("var %s %s = lar_str_from_go_str(%s)" % (_gen_str_literal_name(t), _STR_TYPE_NAME_CODE, _gen_str_literal(t.value)))
        for t in module.literal_number_list:
            assert (t.is_literal and t.type[8 :] in ("char", "int", "uint", "long", "ulong", "float", "double") and
                    t.id not in _literal_token_id_set)
            _literal_token_id_set.add(t.id)
            code += ("var %s %s = (%s)" %
                     (_gen_number_literal_name(t), _gen_type_name_code(eval("larc_type.%s_TYPE" % t.type[8 :].upper())), t.value))

        code += ""
        for gv in module.global_var_map.itervalues():
            code += "var %s %s = %s" % (_gen_gv_name(gv), _gen_type_name_code(gv.type), _gen_default_value_code(gv.type))

        code += ""
        mod_inited_flag_name = "lar_env_inited_flag_of_mod_%s" % _gen_module_name_code(module)
        code += "var %s bool = false" % mod_inited_flag_name
        with code.new_blk("func %s()" % _gen_init_mod_func_name(module), False):
            with code.new_blk("if !%s" % mod_inited_flag_name):
                code += "%s = true" % mod_inited_flag_name
                for dep_module_name in module.get_dep_module_set():
                    code += "%s()" % _gen_init_mod_func_name(larc_module.module_map[dep_module_name])
                for gv in module.global_var_map.itervalues():
                    if gv.expr is not None:
                        code.record_tb_info(gv.expr.pos_info)
                        code += "%s = %s" % (_gen_gv_name(gv), _gen_expr_code(gv.expr))

        for intf in [i for i in module.intf_map.itervalues() if not i.gtp_name_list] + list(module.gintf_inst_map.itervalues()):
            with code.new_blk("type %s interface" % (_gen_coi_name(intf))):
                for method in intf.method_map.itervalues():
                    code += "%s(%s) %s" % (_gen_method_name_code(method), _gen_arg_def(method.arg_map), _gen_type_name_code(method.type))

        for closure in module.closure_map.itervalues():
            coi_name = _gen_coi_name(closure)
            with code.new_blk("type %s struct" % coi_name):
                for method in closure.method_map.itervalues():
                    code += "cm_%s func (%s) %s" % (method.name, _gen_arg_def(method.arg_map), _gen_type_name_code(method.type))
            for method in closure.method_map.itervalues():
                with code.new_blk("func (this *%s) %s(%s) %s" %
                                  (coi_name, _gen_method_name_code(method), _gen_arg_def(method.arg_map), _gen_type_name_code(method.type))):
                    code.record_tb_info(_POS_INFO_IGNORE)
                    code += ("%sthis.cm_%s(%s)" %
                             ("" if method.type.is_void else "return ", method.name, ", ".join(["l_%s" % name for name in method.arg_map])))
            code += "var lar_reflect_type_name_%s = lar_str_from_go_str(%s)" % (coi_name, _gen_str_literal("<%s>" % closure))
            with code.new_blk("func (this *%s) lar_reflect_type_name() %s" % (coi_name, _STR_TYPE_NAME_CODE)):
                code += "return lar_reflect_type_name_%s" % coi_name

        for file_name, native_code_list in module.global_native_code_map.iteritems():
            code.switch_file(file_name)
            for native_code in native_code_list:
                _output_native_code(code, native_code, "<module>")

        def output_reflect_method(code):
            if cls.module is larc_module.array_module and cls.name == "Arr":
                #数组对象的类型名要处理一下
                assert len(cls.gtp_map) == 1
                cls_type_name = str(cls.gtp_map.value_at(0)) + "[]"
            else:
                cls_type_name = str(cls)
            code += "var lar_reflect_type_name_%s = lar_str_from_go_str(%s)" % (lar_cls_name, _gen_str_literal(cls_type_name))
            with code.new_blk("func (this *%s) lar_reflect_type_name() %s" % (lar_cls_name, _STR_TYPE_NAME_CODE)):
                code += "return lar_reflect_type_name_%s" % lar_cls_name
        for cls in [i for i in module.cls_map.itervalues() if not i.gtp_name_list] + list(module.gcls_inst_map.itervalues()):
            code.switch_file(cls.file_name)
            lar_cls_name = _gen_coi_name(cls)
            output_reflect_method(code)
            with code.new_blk("type %s struct" % (lar_cls_name)):
                for native_code in cls.native_code_list:
                    _output_native_code(code, native_code, "<module>")
                for attr in cls.attr_map.itervalues():
                    code += "m_%s %s" % (attr.name, _gen_type_name_code(attr.type))
            with code.new_blk("func lar_new_obj_%s(%s) *%s" % (lar_cls_name, _gen_arg_def(cls.construct_method.arg_map), lar_cls_name)):
                code += "o := new(%s)" % lar_cls_name
                code.record_tb_info(_POS_INFO_IGNORE)
                code += "o.lar_construct_method_%s(%s)" % (cls.name, ", ".join(["l_%s" % name for name in cls.construct_method.arg_map]))
                code += "return o"
            for method in [cls.construct_method] + list(cls.method_map.itervalues()):
                if method is cls.construct_method:
                    assert method.name == cls.name
                    method_name = "lar_construct_method_" + method.name
                else:
                    method_name = _gen_method_name_code(method)
                with code.new_blk("func (this *%s) %s(%s) %s" %
                                  (lar_cls_name, method_name, _gen_arg_def(method.arg_map), _gen_type_name_code(method.type))):
                    if method.is_method:
                        _output_stmt_list(code, method.stmt_list)
                        code += "return %s" % _gen_default_value_code(method.type)
                    else:
                        assert method.is_usemethod
                        code.record_tb_info((method.attr.type.token, "<usemethod>"))
                        code += ("%sthis.m_%s.%s(%s)" %
                                 ("" if method.type.is_void else "return ", method.attr.name, method_name,
                                  ", ".join(["l_%s" % name for name in method.arg_map])))

        for func in [i for i in module.func_map.itervalues() if not i.gtp_name_list] + list(module.gfunc_inst_map.itervalues()):
            code.switch_file(func.file_name)
            if module.name == "__builtins" and func.name in ("catch_base", "catch"):
                assert not func.arg_map
                arg_def = "_go_recovered interface{}"
            else:
                arg_def = _gen_arg_def(func.arg_map)
            with code.new_blk("func %s(%s) %s" % (_gen_func_name(func), arg_def, _gen_type_name_code(func.type))):
                _output_stmt_list(code, func.stmt_list)
                code += "return %s" % _gen_default_value_code(func.type)

def _output_util():
    #拷贝runtime中固定的util代码
    util_fix_file_path_name = runtime_dir + "/lar_util.go"
    if not os.path.exists(util_fix_file_path_name):
        larc_common.exit("runtime文件缺失，请检查编译器环境：[%s]" % util_fix_file_path_name)
    f = open("%s/%s.util_fix.go" % (_out_prog_dir, _prog_module_name), "w")
    print >> f, "package %s" % _prog_module_name
    print >> f
    f.write(larc_common.open_src_file(util_fix_file_path_name).read())
    f.close()

    #生成util代码
    with _Code("%s/%s.util.go" % (_out_prog_dir, _prog_module_name)) as code:
        #生成数组相关代码
        for tp in larc_type.array_type_set:
            assert tp.is_array
            arr_type = larc_type.gen_arr_type(tp)
            dim_count = tp.array_dim_count
            while tp.is_array:
                tp = tp.to_elem_type()
            tp_name = _gen_non_array_type_name(tp)
            #数组结构体名和元素类型的code
            arr_tp_name = _gen_arr_tp_name(tp_name, dim_count)
            code += "type %s = %s" % (arr_tp_name, _gen_type_name_code_without_star(arr_type))

        #traceback信息
        with code.new_blk("var lar_util_tb_map = map[lar_util_go_tb]*lar_util_lar_tb"):
            for (go_file_name, go_line_no), tb_info in _tb_map.iteritems():
                if tb_info is None:
                    code += "lar_util_go_tb{file: %s, line: %d}: nil," % (_gen_str_literal(go_file_name), go_line_no)
                else:
                    lar_file_name, lar_line_no, lar_fom_name = tb_info
                    code += ("lar_util_go_tb{file: %s, line: %d}: &lar_util_lar_tb{file: %s, line: %d, fom_name: %s}," %
                             (_gen_str_literal(go_file_name), go_line_no, _gen_str_literal(lar_file_name), lar_line_no,
                              _gen_str_literal(lar_fom_name)))

def _make_prog():
    if platform.system() in ("Darwin", "Linux"):
        try:
            p = subprocess.Popen(["go", "env", "GOPATH"], stdout = subprocess.PIPE)
        except OSError:
            larc_common.exit("无法执行go命令")
        rc = p.wait()
        if rc != 0:
            larc_common.exit("通过go env获取GOPATH失败")
        go_path = p.stdout.read().strip()
        os.environ["GOPATH"] = out_dir + ":" + go_path
        rc = os.system("go build -o %s %s" % (_exe_file, _main_pkg_file))
        if rc != 0:
            larc_common.exit("go build失败")
    else:
        larc_common.exit("不支持在平台'%s'生成可执行程序" % platform.system())

def _run_prog(args_for_run):
    if platform.system() in ("Darwin", "Linux"):
        pass
    else:
        raise Exception("Bug")
    if os.path.exists(_exe_file):
        os.execv(_exe_file, [_exe_file] + args_for_run)
    else:
        larc_common.exit("找不到可执行文件[%s]" % _exe_file)

_ANY_INTF_TYPE_NAME_CODE = None
_STR_TYPE_NAME_CODE = None

def output(need_run_prog, args_for_run):
    global _ANY_INTF_TYPE_NAME_CODE, _STR_TYPE_NAME_CODE
    _ANY_INTF_TYPE_NAME_CODE = _gen_type_name_code(larc_type.ANY_INTF_TYPE)
    _STR_TYPE_NAME_CODE = _gen_type_name_code(larc_type.STR_TYPE)

    global _out_prog_dir, _prog_module_name, _prog_name, _exe_file, _main_pkg_file, _curr_module

    _out_prog_dir = "%s/src/lar_prog_%s" % (out_dir, _gen_module_name_code(larc_module.module_map[main_module_name]))

    shutil.rmtree(out_dir, True)
    os.makedirs(_out_prog_dir)

    _prog_module_name = "lar_prog_" + _gen_module_name_code(larc_module.module_map[main_module_name])

    _prog_name = main_module_name.split("/")[-1]
    _exe_file = "%s/%s" % (out_dir, _prog_name)

    _main_pkg_file = "%s/src/lar_prog.%s.P.go" % (out_dir, _prog_name)

    _output_main_pkg()
    _output_booter()
    for _curr_module in larc_module.module_map.itervalues():
        _output_module()
    _output_util()
    _make_prog()
    if need_run_prog:
        _run_prog(args_for_run)
