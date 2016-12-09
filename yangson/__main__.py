# Copyright © 2016 CZ.NIC, z. s. p. o.
#
# This file is part of Yangson.
#
# Yangson is free software: you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# Yangson is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with Yangson.  If not, see <http://www.gnu.org/licenses/>.

"""This module defines the entry point for a validation script."""

import argparse
import json
import sys
from typing import List
from yangson import DataModel
from yangson.context import (BadYangLibraryData, FeaturePrerequisiteError,
                                 MultipleImplementedRevisions, ModuleNotFound)
from yangson.enumerations import ContentType, ValidationScope
from yangson.schema import (RawMemberError, RawTypeError, SchemaError,
                                SemanticError)

def main(ylib: str = None, path: List[str] = ["."],
             scope: ValidationScope = ValidationScope.all,
             ctype: ContentType = ContentType.config, set_id: bool = False,
             tree: bool = False, digest: bool = False, validate: str = None):
    """Entry-point for a validation script."""
    if ylib is None:
        parser = argparse.ArgumentParser(
            prog="yangson",
            description="Validate JSON data against a YANG data model.")
        parser.add_argument(
            "ylib", metavar="YLIB",
            help=("name of the file with description of the data model"
                      " in JSON-encoded YANG library format [RFC 7895]"))
        parser.add_argument(
            "-p", "--path", default=".",
            help=("colon-separated list of directories to search"
                      " for YANG modules"))
        grp = parser.add_mutually_exclusive_group()
        grp.add_argument(
            "-i", "--id", action="store_true",
            help="print module set id")
        grp.add_argument(
            "-t", "--tree", action="store_true",
            help="print schema tree as ASCII art")
        grp.add_argument(
            "-d", "--digest", action="store_true",
            help="print schema digest in JSON format")
        grp.add_argument(
            "-v", "--validate", metavar="INST",
            help="name of the file with JSON-encoded instance data")
        parser.add_argument(
            "-s", "--scope", choices=["syntax", "semantics", "all"],
            default="all", help="validation scope")
        parser.add_argument(
            "-c", "--ctype", type=str, choices=["config", "nonconfig", "all"],
            default="config", help="content type of the data instance")
        args = parser.parse_args()
        ylib = args.ylib
        set_id = args.id
        tree = args.tree
        digest = args.digest
        validate = args.validate
        path = args.path.split(":")
        scope = ValidationScope[args.scope]
        ctype = ContentType[args.ctype]
    try:
        with open(ylib, encoding="utf-8") as infile:
            yl = infile.read()
    except (FileNotFoundError, PermissionError,
                json.decoder.JSONDecodeError) as e:
        print("YANG library:" , str(e), file=sys.stderr)
        return 1
    try:
        dm = DataModel(yl, path)
    except BadYangLibraryData as e:
        print("Invalid YANG library:" , str(e), file=sys.stderr)
        return 2
    except FeaturePrerequisiteError as e:
        print("Unsupported pre-requisite feature:" , str(e), file=sys.stderr)
        return 2
    except MultipleImplementedRevisions as e:
        print("Multiple implemented revisions:" , str(e), file=sys.stderr)
        return 2
    except ModuleNotFound as e:
        print("Module not found:" , str(e), file=sys.stderr)
        return 2
    if set_id:
        print(dm.module_set_id())
        return 0
    if tree:
        print(dm.ascii_tree())
        return 0
    if tree:
        print(dm.ascii_tree())
        return 0
    if digest:
        print(dm.schema_digest())
        return 0
    if not validate:
        return 0
    try:
        with open(validate, encoding="utf-8") as infile:
            itxt = json.load(infile)
    except (FileNotFoundError, PermissionError,
                json.decoder.JSONDecodeError) as e:
        print("Instance data:" , str(e), file=sys.stderr)
        return 1
    try:
        i = dm.from_raw(itxt)
    except RawMemberError as e:
        print("Illegal object member:" , str(e), file=sys.stderr)
        return 3
    except RawTypeError as e:
        print("Wrong type:" , str(e), file=sys.stderr)
        return 3
    try:
        i.validate(scope, ctype)
    except SchemaError as e:
        print("Schema error:" , str(e), file=sys.stderr)
        return 3
    except SemanticError as e:
        print("Semantic error:" , str(e), file=sys.stderr)
        return 3
    return 0

sys.exit(main())