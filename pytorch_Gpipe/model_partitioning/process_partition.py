from ..model_profiling import Graph, NodeTypes


def post_process_partition(graph: Graph, part, weights=None):
    set_partition(graph, part)

    ensure_graph_validity(graph)

    nparts = len({n.part for n in graph.nodes})
    # make sure the inputs to an OP type node are all in the same part
    OP_inputs_partition_correction(graph, nparts)
    # make sure every scc in the graph is not splitted between different parts
    scc_partition_correction(graph)


def OP_inputs_partition_correction(graph: Graph, nparts):
    groups = []
    for v in graph.nodes:
        if v.type == NodeTypes.OP:
            group = {u for u in v.in_nodes}
            group.add(v)

            if len({u.part for u in group}) > 1:
                groups.append(group)

    nodes_left = [v for v in graph.nodes]
    # check and update for every group
    for group in groups:
        min_comunication = float("inf")
        best_part = -1
        for part in range(nparts):
            # try a part
            for u in group:
                graph.nodes[u.idx].part = part
            # compute how good it is
            comunication = compute_comunication(graph)
            # update part if he is better
            if comunication < min_comunication:
                min_comunication = comunication
                best_part = part
        # update part to the best part and remove it from nodes to check
        for u in group:
            if u in nodes_left:
                graph.nodes[u.idx].part = best_part
                nodes_left.remove(u)


def compute_comunication(graph: Graph):
    count = 0
    for v in graph.nodes:
        for u in v.in_nodes:
            if u.part != v.part:
                count += 1
    return count


def scc_partition_correction(graph: Graph):
    # create the scc graph
    vertices = [v.idx for v in graph.nodes]
    edges = {}
    for v in graph.nodes:
        idx_out_nodes = [h.idx for h in v.out_nodes]
        edges.update({v.idx: idx_out_nodes})

    for scc in strongly_connected_components_iterative(vertices, edges):
        # check if the scc is splitted between 2 parts or more
        scc_parts = []
        for v in scc:
            if graph.nodes[v].part not in scc_parts:
                scc_parts.append(graph.nodes[v].part)
            if len(scc_parts) >= 2:
                break
        # if he is splitted:
        if len(scc_parts) >= 2:
            output_part = -1
            # find out what part edges go to from this scc
            for v in scc:
                for out in graph.nodes[v].out_nodes:
                    if out.idx not in scc:
                        output_part = graph.nodes[out.idx].part
                        break
                if output_part != -1:
                    break
            # update the scc part to the part we found
            for v in scc:
                graph.nodes[v].part = output_part


def strongly_connected_components_iterative(vertices, edges):
    identified = set()
    stack = []
    index = {}
    boundaries = []

    for v in vertices:
        if v not in index:
            to_do = [('VISIT', v)]
            while to_do:
                operation_type, v = to_do.pop()
                if operation_type == 'VISIT':
                    index[v] = len(stack)
                    stack.append(v)
                    boundaries.append(index[v])
                    to_do.append(('POSTVISIT', v))
                    # We reverse to keep the search order identical to that of
                    # the recursive code;  the reversal is not necessary for
                    # correctness, and can be omitted.
                    to_do.extend(
                        reversed([('VISITEDGE', w) for w in edges[v]]))
                elif operation_type == 'VISITEDGE':
                    if v not in index:
                        to_do.append(('VISIT', v))
                    elif v not in identified:
                        while index[v] < boundaries[-1]:
                            boundaries.pop()
                else:
                    # operation_type == 'POSTVISIT'
                    if boundaries[-1] == index[v]:
                        boundaries.pop()
                        scc = set(stack[index[v]:])
                        del stack[index[v]:]
                        identified.update(scc)
                        yield scc


def set_partition(graph: Graph, parts):
    for node, part in zip(graph.nodes, parts):
        node.part = part


def ensure_graph_validity(graph: Graph):
    op_nodes = filter(lambda n: n.type == NodeTypes.OP, graph.nodes)

    for node in op_nodes:
        if any((in_node.type == NodeTypes.OP and in_node.part != node.part) for in_node in node.in_nodes):
            print("we have discovered 2 arithmetic ops that reside on different devices\n we recommend using a smaller depth or using more general basic blocks")
            return