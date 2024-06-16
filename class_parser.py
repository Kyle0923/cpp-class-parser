#!/usr/bin/env python3
import os
import json
import re
import argparse
import clang.cindex
import graphviz

# workspace root
# any types defined under this directory will be considered project definition instead of system definition
WS_ROOT = ""

# when finished parsing the directory, this script will dump the result to this json file
# the second run of this script will use this json file instead, this will save time for parsing
# use --rebuild to regenerate this json file when source code is updated
CLASS_GRAPH_DB_JSON = "class_graph_db.json"

def is_project_defined_type(node):
    # Ensure both paths are absolute
    if node.location.file:
        file_path = os.path.abspath(node.location.file.name)
        return file_path.startswith(project_dir) or (WS_ROOT and file_path.startswith(WS_ROOT))
    return False

def find_classes(node, classes):
    if node.kind in [clang.cindex.CursorKind.CLASS_DECL, clang.cindex.CursorKind.STRUCT_DECL]:
        if node.is_definition() and is_project_defined_type(node):
            class_name = node.spelling
            base_classes = [re.sub("^(class|struct)\\s+", "", base.spelling) for base in node.get_children() if base.kind == clang.cindex.CursorKind.CXX_BASE_SPECIFIER]
            if base_classes:
                classes[class_name] = base_classes
    for child in node.get_children():
        find_classes(child, classes)

def parse_file(filename, index):
    try:
        additional_options = ['-x', 'c++-header'] # treat .h as c++ header
        abs_path = os.path.abspath(filename)
        if abs_path in compile_db:
            additional_options += compile_db[abs_path]
        else:
            additional_options += compile_default_options.keys()

        translation_unit = index.parse(filename, additional_options)
    except Exception as e:
        print(f"Error parsing file {filename}: {e}")
        return {}

    classes = {}
    find_classes(translation_unit.cursor, classes)
    return classes

def find_descendants(parent_dict: dict):
    child_dict = {}
    for derived, bases in parent_dict.items():
        if derived not in child_dict:
            child_dict[derived] = []
        for base in bases:
            if base not in child_dict:
                child_dict[base] = []
            child_dict[base].append(derived)
    return child_dict

def print_descendants(child_dict: dict, classes: list|str):
    if not classes:
        classes = [c for c in child_dict.keys() if all(c not in children for children in child_dict.values())]
    print("#######################################################")
    print("printing descendants")
    print('============================')
    print_tree(child_dict, classes, "", "descendants")
    print("#######################################################")
    print()


def print_ancestors(parent_dict: dict, classes: list|str):
    if not classes:
        classes = [p for p in parent_dict.keys() if all(p not in parent for parent in parent_dict.values())]
    print("#######################################################")
    print("printing ancestors")
    print('============================')
    print_tree(parent_dict, classes, "", "ancestors")
    print("#######################################################")
    print()

def print_tree(connection: dict, nodes: list|str, indent: str = "", msg: str = ""):
    if isinstance(nodes, str):
        nodes = [nodes]

    for node in nodes:
        is_top = (indent == "")
        next_level_nodes = connection.get(node, [])
        if is_top:
            if not next_level_nodes:
                print(node, f": no {msg} found")
                return
            else:
                print(node)

        for i, next_node in enumerate(next_level_nodes):
            is_last = (i == len(next_level_nodes) - 1)
            prefix = "└── " if is_last else "├── "
            print(indent + prefix + next_node)
            print_tree(connection, next_node, indent + ("    " if is_last else "│   ") )

        if is_top:
            print('----------------------------')

# generate a graph view of the class hierarchy using Graphviz
def generate_graph(parent_dict: dict, child_dict: dict, nodes: str | list, dot: graphviz.Digraph, inserted: dict):

    if isinstance(nodes, str):
        nodes = [nodes]

    for curr_node in nodes:
        # travese towards base
        if parent_dict and args.base:
            for other_node in parent_dict.get(curr_node, []):
                inserted[other_node] = True
                edge_key = f"{other_node}->{curr_node}"
                if edge_key in inserted:
                    continue

                inserted[edge_key] = True
                dot.edge(other_node, curr_node)
                if args.connected:
                    generate_graph(parent_dict, child_dict, other_node, dot, inserted)
                else:
                    # don't need the child of the parent
                    generate_graph(parent_dict, None, other_node, dot, inserted)

        # travese towards derived
        if child_dict and args.derived:
            for other_node in child_dict.get(curr_node, []):
                inserted[other_node] = True
                edge_key = f"{curr_node}->{other_node}"
                if edge_key in inserted:
                    continue

                inserted[edge_key] = True
                dot.edge(curr_node, other_node)
                if args.connected:
                    generate_graph(parent_dict, child_dict, other_node, dot, inserted)
                else:
                    # don't need the parent of the child
                    generate_graph(None, child_dict, other_node, dot, inserted)

def get_compile_options(dir: str):

    # when a file doesn't have entry in compile_commands.json or the json doesn't exist
    # will use compile_default_options
    global compile_default_options
    compile_default_options = {}

    global compile_db
    compile_db = {}

    if not args.compile_db:
        args.compile_db = os.path.join(dir, "compile_commands.json")
    if os.path.isfile(args.compile_db):
        parse_compile_commands_json(args.compile_db)

    guess_incl_path(dir)

def parse_compile_commands_json(file: str):
    with open(file) as fd:
        json_db = json.load(fd)

    for obj in json_db:
        compile_options = trim_compile_options(obj["arguments"])
        abs_path = os.path.abspath(obj["file"])
        compile_db[abs_path] = compile_options

# only keep -I, -D, and -std options
def trim_compile_options(options: list):
    trimmed = []
    for opt in options:
        if opt.startswith("-I") or opt.startswith("-D") or opt.startswith("-std"):
            trimmed.append(opt)
            compile_default_options[opt] = True
    return trimmed

def guess_incl_path(dir: str):
    for dirpath, _, files in os.walk(dir):
        for file in files:
            if file.endswith('.h') or file.endswith('.hpp'):
                compile_default_options[f"-I{dirpath}"] = True
                break

def tree_report(parent_dict, query):
    if args.base:
        print_ancestors(parent_dict, query)
    if args.derived:
        child_dict = find_descendants(parent_dict)
        print_descendants(child_dict, query)

def graph_report(parent_dict: dict, query):
    dot = graphviz.Digraph()
    dot.node_attr["shape"] = "box"
    dot.node_attr["style"] = "rounded"
    inserted = {}

    for node in query:
        dot.node(node, style="filled, rounded", fillcolor="turquoise")

    if not query:
        # print all nodes and edges
        generate_graph(parent_dict, None, parent_dict.keys(), dot, inserted)
    else:
        child_dict = find_descendants(parent_dict)
        generate_graph(parent_dict, child_dict, query, dot, inserted)

    dot.render('class_graph', format='pdf')
    print("use https://dreampuf.github.io/GraphvizOnline/ to view graph")
    print("graph file is at ./class_graph")

def generate_parent_dict(dir: str):
    full_json_db_path = os.path.join(dir, CLASS_GRAPH_DB_JSON)
    if os.path.isfile(full_json_db_path) and args.rebuild == False:
        with open(full_json_db_path) as fd:
            print(f"[[ {full_json_db_path} ]] exists, skip parsing source code, use --rebuild to force parsing source\n")
            parent_dict = json.load(fd)
            return parent_dict

    global project_dir
    project_dir = os.path.abspath(dir)

    get_compile_options(dir)

    index = clang.cindex.Index.create()
    parent_dict = {} # key: class, value: base class
    for root, _, files in os.walk(dir):
        for file in files:
            if file.endswith('.cpp') or file.endswith('.h') or file.endswith('.hpp'):
                filepath = os.path.join(root, file)
                classes = parse_file(filepath, index)
                parent_dict.update(classes)

    with open(full_json_db_path, 'w') as fd:
        json.dump(parent_dict, fd)

    return parent_dict

def main(dir):
    parent_dict = generate_parent_dict(dir)
    query = args.classes
    if args.tree:
        tree_report(parent_dict, query)
    else:
        graph_report(parent_dict, query)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='generate the inheritance hierarchy')
    parser.add_argument('--rebuild', action='store_true', help="regenerate the json database, use when source code is modify")
    parser.add_argument('--compile_db', metavar="compile_commands.json", help="JSON Compilation Database in Clang Format, will attempt to use ./compile_commands.json when not provided")
    parser.add_argument('--path', help="path to workspace root, defult is current directory")
    parser.add_argument('--tree', action='store_true', help="output in tree view instead of graph view")
    parser.add_argument('-b', '--base', action='store_true', help="only print the ancestor classes")
    parser.add_argument('-d', '--derived', action='store_true', help="only print the descendant classes")
    parser.add_argument('-r', '--related', action='store_true', help="print both the ancestor and descendant classes, this is the default")
    parser.add_argument('-c', '--connected', '--all', action='store_true', help="print all classes that are connected to any of the ancestor and descendant classes, only available in graph report")

    parser.add_argument('classes', nargs='*', help="name(s) of the querying class")


    global args
    args = parser.parse_args()

    # process --derived vs --base vs --related, use --related as default
    if args.derived == False and args.base == False:
        args.related = True
    if args.related == True or args.connected == True:
        args.base = True
        args.derived = True

    if not args.path:
        args.path = os.getcwd() # Use the current directory if no argument is provided

    main(args.path)
