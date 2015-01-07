# Copyright (c) 2012 Yuri K. Schlesner
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import unicode_literals
from util import DecompilerBase, First, WordConcatenator, reconstruct_paraminfo, reconstruct_arginfo, string_escape, split_logical_lines

import magic
magic.fake_package(b"renpy")
import renpy

import screendecompiler
import sl2decompiler
import codegen
import astdump

__all__ = ["astdump", "codegen", "magic", "screendecompiler", "sl2decompiler", "util", "pprint", "Decompiler"]

# Main API

def pprint(out_file, ast, indent_level=0,
           force_multiline_kwargs=True, decompile_screencode=True,
           decompile_python=True, comparable=False):
    Decompiler(out_file,
               force_multiline_kwargs=force_multiline_kwargs,
               decompile_screencode=decompile_screencode,
               decompile_python=decompile_python,
               comparable=comparable).dump(ast, indent_level)

# Implementation

class Decompiler(DecompilerBase):
    """
    An object which hanldes the decompilation of renpy asts to a given stream
    """

    # This dictionary is a mapping of Class: unbount_method, which is used to determine
    # what method to call for which ast class
    dispatch = {}

    def __init__(self, out_file=None, force_multiline_kwargs=True, decompile_screencode=True,
                 decompile_python=True, indentation = '    ', comparable=False):
        super(Decompiler, self).__init__(out_file, indentation, comparable)
        self.force_multiline_kwargs = force_multiline_kwargs
        self.decompile_screencode = decompile_screencode
        self.decompile_python = decompile_python

        self.paired_with = False
        self.say_inside_menu = None

    def dump(self, ast, indent_level=0):
        if not self.comparable:
            self.write("# Decompiled by unrpyc (https://github.com/CensoredUsername/unrpyc")
        # Avoid an initial blank line if we don't write out the above banner
        super(Decompiler, self).dump(ast, indent_level, skip_indent_until_write=self.comparable)
        self.write("\n") # end file with a newline

    def print_node(self, ast):
        # We special-case line advancement for TranslateString in its print
        # method, so don't advance lines for it here.
        if hasattr(ast, 'linenumber') and not isinstance(ast, renpy.ast.TranslateString):
            self.advance_to_line(ast.linenumber)
        # It doesn't matter what line "block:" is on. The loc of a RawBlock
        # refers to the first statement inside the block, which we advance
        # to from print_atl.
        elif hasattr(ast, 'loc') and not isinstance(ast, renpy.atl.RawBlock):
            self.advance_to_line(ast.loc[1])
        func = self.dispatch.get(type(ast), None)
        if func:
            func(self, ast)
        else:
            # This node type is unknown
            self.print_unknown(ast)

    # ATL printing functions

    # TODO "choice" and "parallel" blocks are greedily combined
    #      so we need a "pass" statement to separate them if
    #      multiple of the same block are immediately after
    #      each other.
    def print_atl(self, ast):
        self.advance_to_line(ast.loc[1])
        self.indent_level += 1
        if ast.statements:
            self.print_nodes(ast.statements)
        # If a statement ends with a colon but has no block after it, loc will
        # get set to ('', 0). That isn't supposed to be valid syntax, but it's
        # the only thing that can generate that.
        elif not self.comparable or ast.loc != ('', 0):
            self.indent()
            self.write("pass")
        self.indent_level -= 1

    def print_atl_rawmulti(self, ast):
        self.indent()
        words = WordConcatenator(False)

        # warpers
        if ast.warp_function:
            words.append("warp", ast.warp_function, ast.duration)
        elif ast.warper:
            words.append(ast.warper, ast.duration)
        elif ast.duration != "0":
            words.append("pause", ast.duration)

        # revolution
        if ast.revolution:
            words.append(ast.revolution)

        # circles
        if ast.circles != "0":
            words.append("circles", ast.circles)

        # splines
        for name, expressions in ast.splines:
            words.append(name)
            for expression in expressions:
                words.append("knot", expression)

        # properties
        for key, value in ast.properties:
            words.append(key, value)

        # with
        for (expression, with_expression) in ast.expressions:
            words.append(expression)
            if with_expression:
                words.append("with", with_expression)

        self.write(words.join())
    dispatch[renpy.atl.RawMultipurpose] = print_atl_rawmulti

    def print_atl_rawblock(self, ast):
        self.indent()
        self.write("block:")
        self.print_atl(ast)
    dispatch[renpy.atl.RawBlock] = print_atl_rawblock

    def print_atl_rawchild(self, ast):
        for child in ast.children:
            self.indent()
            self.write("contains:")
            self.print_atl(child)
    dispatch[renpy.atl.RawChild] = print_atl_rawchild

    def print_atl_rawchoice(self, ast):
        for chance, block in ast.choices:
            self.indent()
            self.write("choice")
            if chance != "1.0":
                self.write(" %s" % chance)
            self.write(":")
            self.print_atl(block)
    dispatch[renpy.atl.RawChoice] = print_atl_rawchoice

    def print_atl_rawcontainsexpr(self, ast):
        self.indent()
        self.write("contains %s" % ast.expression)
    dispatch[renpy.atl.RawContainsExpr] = print_atl_rawcontainsexpr

    def print_atl_rawevent(self, ast):
        self.indent()
        self.write("event %s" % ast.name)
    dispatch[renpy.atl.RawEvent] = print_atl_rawevent

    def print_atl_rawfunction(self, ast):
        self.indent()
        self.write("function %s" % ast.expr)
    dispatch[renpy.atl.RawFunction] = print_atl_rawfunction

    def print_atl_rawon(self, ast):
        if self.comparable:
            # Make sure we iterate over the events in order of original appearance
            events_to_iterate = sorted(ast.handlers.items(), key=lambda i: i[1].loc[1])
        else:
            events_to_iterate = ast.handlers.iteritems()
        for name, block in events_to_iterate:
            self.indent()
            self.write("on %s:" % name)
            self.print_atl(block)
    dispatch[renpy.atl.RawOn] = print_atl_rawon

    def print_atl_rawparallel(self, ast):
        for block in ast.blocks:
            self.indent()
            self.write("parallel:")
            self.print_atl(block)
    dispatch[renpy.atl.RawParallel] = print_atl_rawparallel

    def print_atl_rawrepeat(self, ast):
        self.indent()
        self.write("repeat")
        if ast.repeats:
            self.write(" %s" % ast.repeats) # not sure if this is even a string
    dispatch[renpy.atl.RawRepeat] = print_atl_rawrepeat

    def print_atl_rawtime(self, ast):
        self.indent()
        self.write("time %s" % ast.time)
    dispatch[renpy.atl.RawTime] = print_atl_rawtime

    # Displayable related functions

    def print_imspec(self, imspec):
        if imspec[1] is not None:
            self.write("expression %s" % imspec[1])
        else:
            self.write(" ".join(imspec[0]))

        if imspec[2] is not None:
            self.write(" as %s" % imspec[2])

        if len(imspec[6]) > 0:
            self.write(" behind %s" % ', '.join(imspec[6]))

        if imspec[4] != "master":
            self.write(" onlayer %s" % imspec[4])

        if imspec[5] is not None:
            self.write(" zorder %s" % imspec[5])

        # Prior to Ren'Py 6.17, simple expressions didn't have trailing spaces
        # trimmed, so the last imspec[3] value may end with a space. If it does,
        # return true so the caller knows not to add another one (which would
        # otherwise show up in the AST).
        if len(imspec[3]) > 0:
            self.write(" at %s" % ', '.join(imspec[3]))
            return imspec[3][-1][-1] == ' '
        return False

    def print_image(self, ast):
        self.indent()
        self.write("image %s" % ' '.join(ast.imgname))
        if ast.code is not None:
            self.write(" = %s" % ast.code.source)
        else:
            if hasattr(ast, "atl") and ast.atl is not None:
                self.write(":")
                self.print_atl(ast.atl)
    dispatch[renpy.ast.Image] = print_image

    def print_transform(self, ast):
        self.indent()
        self.write("transform %s" % ast.varname)
        if ast.parameters is not None:
            self.write(reconstruct_paraminfo(ast.parameters))

        if hasattr(ast, "atl") and ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)
    dispatch[renpy.ast.Transform] = print_transform

    # Directing related functions

    def print_show(self, ast):
        self.indent()
        self.write("show ")
        needs_space = not self.print_imspec(ast.imspec)

        if self.paired_with:
            if needs_space:
                self.write(" ")
            self.write("with %s" % self.paired_with)
            self.paired_with = True

        if hasattr(ast, "atl") and ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)
    dispatch[renpy.ast.Show] = print_show

    def print_showlayer(self, ast):
        self.indent()
        self.write("show layer %s" % ast.layer)

        if ast.at_list:
            self.write(" at %s" % ', '.join(ast.at_list))

        if hasattr(ast, "atl") and ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)
    dispatch[renpy.ast.ShowLayer] = print_showlayer

    def print_scene(self, ast):
        self.indent()
        self.write("scene")

        if ast.imspec is None:
            if ast.layer != "master":
                self.write(" onlayer %s" % ast.layer)
            needs_space = True
        else:
            self.write(" ")
            needs_space = not self.print_imspec(ast.imspec)

        if self.paired_with:
            if needs_space:
                self.write(" ")
            self.write("with %s" % self.paired_with)
            self.paired_with = True

        if hasattr(ast, "atl") and ast.atl is not None:
            self.write(":")
            self.print_atl(ast.atl)
    dispatch[renpy.ast.Scene] = print_scene

    def print_hide(self, ast):
        self.indent()
        self.write("hide ")
        self.print_imspec(ast.imspec)
    dispatch[renpy.ast.Hide] = print_hide

    def print_with(self, ast):
        # the 'paired' attribute indicates that this with
        # and with node afterwards are part of a postfix
        # with statement. detect this and process it properly
        if hasattr(ast, "paired") and ast.paired is not None:
            # Sanity check. check if there's a matching with statement two nodes further
            if not(isinstance(self.block[self.index + 2], renpy.ast.With) and
                   self.block[self.index + 2].expr == ast.paired):
                raise Exception("Unmatched paired with {0} != {1}".format(
                                repr(self.paired_with), repr(ast.expr)))

            self.paired_with = ast.paired

        elif self.paired_with:
            # Check if it was consumed by a show/scene statement
            if self.paired_with is not True:
                self.write(" with %s" % ast.expr)
            self.paired_with = False
        else:
            self.indent()
            self.write("with %s" % ast.expr)
            self.paired_with = False
    dispatch[renpy.ast.With] = print_with

    # Flow control

    def print_label(self, ast):
        if self.index and isinstance(self.block[self.index - 1], renpy.ast.Call):
            self.write(" from %s" % ast.name)
        else:
            self.indent()
            self.write("label %s%s:" % (ast.name, reconstruct_paraminfo(ast.parameters)))
            self.print_nodes(ast.block, 1)
    dispatch[renpy.ast.Label] = print_label

    def print_jump(self, ast):
        self.indent()
        self.write("jump ")
        if ast.expression:
            self.write("expression %s" % ast.target)
        else:
            self.write(ast.target)
    dispatch[renpy.ast.Jump] = print_jump

    def print_call(self, ast):
        self.indent()
        self.write("call ")
        if ast.expression:
            self.write("expression %s" % ast.label)
        else:
            self.write(ast.label)

        if ast.arguments is not None:
            if ast.expression:
                self.write(" pass ")
            self.write(reconstruct_arginfo(ast.arguments))
    dispatch[renpy.ast.Call] = print_call

    def print_return(self, ast):
        self.indent()
        self.write("return")

        if ast.expression is not None:
            self.write(" %s" % ast.expression)
    dispatch[renpy.ast.Return] = print_return

    def print_if(self, ast):
        statement = First("if %s:", "elif %s:")

        for i, (condition, block) in enumerate(ast.entries):
            self.indent()
            if (i + 1) == len(ast.entries) and condition.strip() == "True":
                self.write("else:")
            else:
                self.write(statement() % condition)

            self.print_nodes(block, 1)
    dispatch[renpy.ast.If] = print_if

    def print_while(self, ast):
        self.indent()
        self.write("while %s:" % ast.condition)

        self.print_nodes(ast.block, 1)
    dispatch[renpy.ast.While] = print_while

    def print_pass(self, ast):
        if not(self.index and
               isinstance(self.block[self.index - 1], renpy.ast.Call)):
            self.indent()
            self.write("pass")
    dispatch[renpy.ast.Pass] = print_pass

    def should_come_before(self, first, second):
        return self.comparable and first.linenumber < second.linenumber

    def print_init(self, ast):
        # A bunch of statements can have implicit init blocks
        # Define has a default priority of 0, screen of -500 and image of 990
        if len(ast.block) == 1 and (
            (ast.priority == -500 and isinstance(ast.block[0], renpy.ast.Screen)) or
            (ast.priority == 0 and isinstance(ast.block[0], (renpy.ast.Define,
                                                             renpy.ast.Transform,
                                                             renpy.ast.Style))) or
            (ast.priority == 990 and isinstance(ast.block[0], renpy.ast.Image))) and not (
            self.should_come_before(ast, ast.block[0])):
            # If they fulfil this criteria we just print the contained statement
            self.print_nodes(ast.block)

        # translatestring statements are split apart and put in an init block.
        elif (len(ast.block) > 0 and
                ast.priority == 0 and
                all(isinstance(i, renpy.ast.TranslateString) for i in ast.block) and
                all(i.language == ast.block[0].language for i in ast.block[1:])):
            self.print_nodes(ast.block)

        else:
            self.indent()
            self.write("init")
            if ast.priority:
                self.write(" %d" % ast.priority)

            if len(ast.block) == 1 and ast.linenumber >= ast.block[0].linenumber:
                self.write(" ")
                self.skip_indent_until_write = True
                self.print_nodes(ast.block)
            else:
                self.write(":")
                self.print_nodes(ast.block, 1)

    dispatch[renpy.ast.Init] = print_init

    def print_menu(self, ast):
        self.indent()
        self.write("menu:")
        self.indent_level += 1

        if self.say_inside_menu is not None:
            self.print_say(self.say_inside_menu, inmenu=True)
            self.say_inside_menu = None

        if ast.with_ is not None:
            self.indent()
            self.write("with %s" % ast.with_)

        if ast.set is not None:
            self.indent()
            self.write("set %s" % ast.set)

        for label, condition, block in ast.items:
            self.indent()
            self.write('"%s"' % string_escape(label))

            if block is not None:
                if condition != 'True':
                    self.write(" if %s" % condition)

                self.write(":")

                self.print_nodes(block, 1)

        self.indent_level -= 1
    dispatch[renpy.ast.Menu] = print_menu

    # Programming related functions

    def print_python(self, ast, early=False):
        self.indent()

        code = ast.code.source
        if code[0] == '\n':
            code = code[1:]
            self.write("python")
            if early:
                self.write(" early")
            if ast.hide:
                self.write(" hide")
            self.write(":")

            self.indent_level += 1
            for line in split_logical_lines(code):
                self.indent()
                self.write(line)
            self.indent_level -= 1

        else:
            self.write("$ %s" % code)
    dispatch[renpy.ast.Python] = print_python

    def print_earlypython(self, ast):
        self.print_python(ast, early=True)
    dispatch[renpy.ast.EarlyPython] = print_earlypython

    def print_define(self, ast):
        self.indent()
        if not hasattr(ast, "store") or ast.store == "store":
            self.write("define %s = %s" % (ast.varname, ast.code.source))
        else:
            self.write("define %s.%s = %s" % (ast.store, ast.varname, ast.code.source))
    dispatch[renpy.ast.Define] = print_define

    # Specials

    def print_say(self, ast, inmenu=False):
        if (not ast.interact and not inmenu and ast.who is not None and
            ast.with_ is None and ast.attributes is None and
            self.index + 1 < len(self.block)):
            next_block = self.block[self.index + 1]
            if (isinstance(next_block, renpy.ast.Menu) and
                next_block.items[0][2] is not None and
                not self.should_come_before(ast, next_block)):
                self.say_inside_menu = ast
                return
        self.indent()
        if ast.who is not None:
            self.write("%s " % ast.who)
        self.write('"%s"' % string_escape(ast.what))
        if not ast.interact and not inmenu:
            self.write(" nointeract")
        if ast.with_ is not None:
            self.write(" with %s" % ast.with_)
    dispatch[renpy.ast.Say] = print_say

    def print_userstatement(self, ast):
        self.indent()
        self.write(ast.line)
    dispatch[renpy.ast.UserStatement] = print_userstatement

    def print_style(self, ast):
        self.indent()
        self.write("style %s" % ast.style_name)
        kwargs = []

        if ast.parent is not None:
            kwargs.append("is %s" % ast.parent)
        if ast.clear:
            kwargs.append("clear")
        if ast.take is not None:
            kwargs.append("take %s" % ast.take)
        for delname in ast.delattr:
            kwargs.append("del %s" % delname)
        if ast.variant is not None:
            kwargs.append("variant %s" % ast.variant)

        for key, value in ast.properties.iteritems():
            kwargs.append("%s %s" % (key, value))

        if self.comparable:
            self.write(" %s" % " ".join(kwargs))
        else:
            self.write(":")
            self.indent_level += 1
            for i in kwargs:
                self.indent()
                self.write(i)
            self.indent_level -= 1
    dispatch[renpy.ast.Style] = print_style

    # Translation functions

    def print_translate(self, ast):
        self.indent()
        self.write("translate %s %s:" % (ast.language or "None", ast.identifier))

        self.print_nodes(ast.block, 1)
    dispatch[renpy.ast.Translate] = print_translate

    def print_endtranslate(self, ast):
        # an implicitly added node which does nothing...
        pass
    dispatch[renpy.ast.EndTranslate] = print_endtranslate

    def print_translatestring(self, ast):
        # Was the last node a translatestrings node?
        if not(self.index and
               isinstance(self.block[self.index - 1], renpy.ast.TranslateString) and
               self.block[self.index - 1].language == ast.language):
            self.indent()
            self.write("translate %s strings:" % ast.language or "None")

        # TranslateString's linenumber refers to the line with "old", not to the
        # line with "translate %s strings:"
        self.advance_to_line(ast.linenumber)
        self.indent_level += 1

        self.indent()
        self.write('old "%s"' % string_escape(ast.old))
        self.indent()
        self.write('new "%s"' % string_escape(ast.new))

        self.indent_level -= 1
    dispatch[renpy.ast.TranslateString] = print_translatestring

    def print_translateblock(self, ast):
        self.indent()
        self.write("translate %s " % (ast.language or "None"))

        self.skip_indent_until_write = True
        self.print_nodes(ast.block)
    dispatch[renpy.ast.TranslateBlock] = print_translateblock

    # Screens

    def print_screen(self, ast):
        screen = ast.screen
        if isinstance(screen, renpy.screenlang.ScreenLangScreen):
            self.linenumber = screendecompiler.pprint(self.out_file, screen, self.indent_level,
                                    self.linenumber, self.force_multiline_kwargs,
                                    self.decompile_python, self.decompile_screencode,
                                    self.comparable, self.skip_indent_until_write)
	    self.skip_indent_until_write = False

        elif isinstance(screen, renpy.sl2.slast.SLScreen):
            self.linenumber = sl2decompiler.pprint(self.out_file, screen, self.indent_level,
                                    self.linenumber, self.force_multiline_kwargs,
                                    self.decompile_screencode, self.comparable,
                                    self.skip_indent_until_write)
	    self.skip_indent_until_write = False
        else:
            self.print_unknown(screen)
    dispatch[renpy.ast.Screen] = print_screen
