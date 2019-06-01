import os
from model_partition import partition_network_using_profiler
import torch
from sample_models import *


def torchvision_write_traces():
    networks = [models.alexnet, models.resnet18, models.vgg11_bn,
                models.squeezenet1_0, models.inception_v3, models.densenet121]
    for net in networks:
        model = net(pretrained=False).to("cuda:0")
        if net.__name__.find("inception") != -1:
            x = torch.zeros(10, 3, 299, 299)
        else:
            x = torch.zeros(10, 3, 224, 224)
        x = x.to("cuda:0")
        with torch.no_grad():
            trace_graph, _ = torch.jit.get_trace_graph(
                model, x)
            trace_graph = trace_graph.graph()
            trace = trace_graph.__str__()
            import os
            filename = f"{net.__name__}trace"
            directory = f"{os.getcwd()}\\traces"
            if not os.path.exists(directory):
                os.makedirs(directory)
            with open(f"{directory}\\{filename}.txt", "w") as file:
                file.write(trace)


def partition_torchvision():
    networks = [alexnet, resnet18, vgg11_bn, squeezenet1_0,
                inception_v3, densenet121, GoogLeNet, LeNet, WideResNet]
    depth = [0, 1, 100]
    num_partitions = 4
    for net in networks:
        model = net()
        for d in depth:
            if net.__name__.find("inception") != -1:
                graph, _, _ = partition_network_using_profiler(
                    model, num_partitions, torch.zeros(10, 3, 299, 299), max_depth=d)
            else:
                graph, _, _ = partition_network_using_profiler(
                    model, num_partitions, torch.zeros(10, 3, 224, 224), max_depth=d)

            filename = f"{net.__name__} attempted {num_partitions} partitions at depth {d}"

            curr_dir = os.path.dirname(os.path.realpath(__file__))
            out_dir = f"{curr_dir}\\partition_visualization"
            graph.save(directory=out_dir, file_name=filename,
                       show_buffs_params=False, show_weights=False)
            print(filename)


if __name__ == "__main__":
    partition_torchvision()
