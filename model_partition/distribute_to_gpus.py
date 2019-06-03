import torch.nn as nn
from graph.control_flow_graph import Graph , NodeTypes


def mount(device_lst: list, in_device, out_device, graph: Graph , model: nn.Module,depth,basic_block):
    model_class_name = type(model).__name__
    scope_to_part = {node.scopeName:node.part for node in graph.nodes}
    partition_lst = group_by_partition(graph,len(device_lst))
    partition_to_device=part_to_device(device_lst,partition_lst)
    scope_to_dev={scope:partition_to_device[part] for scope,part in scope_to_part.items()}
    move_layers_to_devices(model,depth,model_class_name,basic_block,scope_to_dev)
    move_buffers_params(model,model_class_name,scope_to_dev)
    return model

def part_to_device(device_lst: list,partition_lst: list):
    in_nodes = [node for part in partition_lst for node in part if node.type == NodeTypes.IN]
    out_nodes = [node for part in partition_lst for node in part if len(node.out_nodes) == 0]
    in_part = [ node.part for node in in_nodes ]
    out_part = [ node.part for node in out_nodes ]

    part_lst=[]
    part_lst.append(in_part[0])
    to_part = []
    for device in range(len(device_lst)):
        nodes = [ *lst for idx,lst in enumerate(partition_lst) if idx in part_lst ]
        for node in nodes:
            for o_node in node.out_nodes:
                to_part.append(o_node.part)
        part_lst = part_lst + to_part
        to_part = []
    return { part : device for part, device in zip(part_lst,device_lst)}


def group_by_partition(graph: Graph, nparts):
    lst = [ [] for _ in range(nparts) ]
    for node in graph.nodes:
        lst[node.part].append(node)
    return lst


def move_layers_to_devices(module: nn.Module, depth, prefix, basic_block,scope_to_dev):
    for name, sub_module in module._modules.items():
        if len(list(sub_module.children())) == 0 or (basic_block != None and isinstance(sub_module, basic_block)) or depth == 0:
                scope=prefix+"/"+type(sub_module).__name__+f"[{name}]"
                sub_module.to(scope_to_dev[scope])
        else:
             move_layers_to_devices(sub_module, depth-1, prefix +"/"+type(sub_module).__name__+f"[{name}]",basic_block,scope_to_dev)


def move_buffers_params(module: nn.Module,prefix,buffer_and_params_scopes_to_dev):
    # params
    for item_name, item in module.named_parameters(recurse=False):
        scopeName=f"{prefix}/{type(item).__name__}[{item_name}]"
        if scopeName in buffer_and_params_scopes_to_dev:
            item.to(buffer_and_params_scopes_to_dev[scopeName])

    # buffs
    for item_name, item in module.named_buffers(recurse=False):
        scopeName=f"{prefix}/{type(item).__name__}[{item_name}]"
        if scopeName in buffer_and_params_scopes_to_dev:
            item.to(buffer_and_params_scopes_to_dev[scopeName])

    # recurse
    for name, sub_module in module._modules.items():
        move_buffers_params(sub_module, prefix +
                                             "/"+type(sub_module).__name__+f"[{name}]",buffer_and_params_scopes_to_dev)

    return names
