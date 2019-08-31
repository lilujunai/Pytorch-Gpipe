from typing import Tuple, Dict, List

import torch
import torch.nn as nn

from ..utils import Tensors, TensorsShape
from .cycle_counter import CycleCounter
from .forward_mode import ForwardMode
from .utils import gen_garbage_output, get_devices, tensors_bi_map, tensors_map, tensors_to


class SyncWrapper(nn.Module):
    """
    A wrapper for layers. used to synchronize between ones which gets their input
    from layers that placed on different GPUs.
    """

    # TODO is num inputs necessary
    def __init__(self, module: nn.Module, device: str, gpu_num: int, output_shapes: TensorsShape,
                 counter: CycleCounter = None):

        super(SyncWrapper, self).__init__()

        self.module = module
        self.device = torch.device(device)
        self.input_devices = None

        self.pipe_stream = torch.cuda.Stream(device=self.device)

        # number of GPU in order of pipeline
        self.gpu_num = gpu_num

        # saved activations of the previous microbatches
        self.activations = []
        # saved RNG states for activations
        self.rng_states = []

        # the inputs saved at the previous cycle, to be passed and switched at the
        # current cycle
        self.prev_inputs = None

        # the grads saved at the previous backward cycle, to be passed and switched
        # at the current cycle
        self.grads = None

        # the cycle counter to check for input validity (garbage vs. actual data)
        self.counter = counter

        # used for garbage-output
        self.output_shapes: TensorsShape = output_shapes

    def set_counter(self, counter: CycleCounter):
        assert self.counter is None

        self.counter = counter

    def has_grads(self):
        return self.prev_inputs is not None

    def pop_activation(self):
        if self.counter.output_valid(self.gpu_num):
            torch.cuda.set_rng_state(self.rng_states.pop(0), self.device)
            self.prev_inputs = self.activations.pop(0)

    def update_grads(self):
        if self.counter.input_valid(self.gpu_num) and self.has_grads():
            with torch.cuda.stream(self.pipe_stream):
                self.grads = tensors_map(
                    self.prev_inputs, lambda act: None if act is None else act.grad)
                self.grads = tensors_to(self.grads, self.input_devices)

    def finished_prop(self):
        """resets data after propagation"""

        self.prev_inputs = None
        self.grads = None

    def backward_mode(self, *inputs: Tensors) -> Tensors:
        """
        function for backward propagation iteration
        """
        # if we were given a gradient to pass back
        if self.counter.input_valid(self.gpu_num):
            tensors_bi_map(inputs, self.grads, lambda input, grad: None if grad is None else input.backward(grad))

        # if we have an activation to pass
        if self.counter.output_valid(self.gpu_num):
            def prepare_activation(act):
                if act is None:
                    return None

                clone = act.requires_grad_().clone()
                clone.retain_grad()
                return clone

            self.prev_inputs = tensors_map(self.prev_inputs, prepare_activation)

            used_inputs = tensors_bi_map(self.prev_inputs, inputs, lambda t1, t2: t2 if t1 is None else t1)

            output = self.module(*used_inputs)
        else:
            output = gen_garbage_output(self.output_shapes, self.device)

            if len(output) == 1:
                output = output[0]

        return output

    def save_activation(self, *moved_inputs: Tensors):
        """saves the activation of the current input"""
        self.rng_states.append(torch.cuda.get_rng_state(self.device))
        self.activations.append(
            tensors_map(moved_inputs, lambda input: None if input is None else input.clone().detach()))

    def delay_device(self, tensor):
        return tensor.device is not self.device

    def forward(self, *inputs: Tensors) -> Tensors:
        # move the input between devices
        if self.counter.cur_mode is ForwardMode.backward:
            return self.backward_mode(*inputs)

        # check if the input that waits for the submodule is relevant (garbage
        # will be propagated before and after data passes through submodule)
        if self.counter.output_valid(self.gpu_num):

            used_inputs = tensors_bi_map(self.prev_inputs, inputs, lambda t1, t2: t2 if t1 is None else t1)

            # the input is relevant.
            output = self.module(*used_inputs)
        else:
            # the input is garbage
            output = gen_garbage_output(self.output_shapes, self.device)

            if len(output) == 1:
                output = output[0]

        # check if the input to be replaced and scheduled to run on the next cycle
        # is relevant or garbage
        if self.counter.input_valid(self.gpu_num):
            with torch.cuda.stream(self.pipe_stream):
                # set the input devices when first actual data is received
                if self.counter.get_count() == self.gpu_num:
                    self.input_devices = get_devices(inputs)

                moved_inputs = tensors_map(inputs, lambda tensor: None if not self.delay_device(tensor) else tensor.to(
                    self.device, non_blocking=True))

                if self.counter.cur_mode is ForwardMode.train:
                    self.save_activation(*moved_inputs)

                self.prev_inputs = moved_inputs
        else:
            self.prev_inputs = tensors_map(inputs, lambda _: None)

        return output

    def __iter__(self):
        return iter(self.module)


class ActivationSavingLayer(nn.Module):
    """
    This class should be put in the very start of the module (i.e Sequential(ActivationSavingLayer, Module))
    """

    # TODO is num inputs necessary

    def __init__(self, device: str, num_inputs=1, counter: CycleCounter = None):
        super(ActivationSavingLayer, self).__init__()

        self.device = torch.device(device)

        self.pipe_stream = torch.cuda.Stream(device=self.device)

        # saved activations of the previous microbatches
        self.activations = []
        # saved RNG states for activations
        self.rng_states = []

        # the inputs saved at the previous cycle, to be passed and switched at the
        # current cycle
        self.prev_inputs = None

        # the cycle counter to check for input validity (garbage vs. actual data)
        self.counter = counter

        self.gpu_num = 0

    def set_counter(self, counter: CycleCounter):
        assert self.counter is None

        self.counter = counter

    def pop_activation(self):
        if self.counter.output_valid(self.gpu_num):
            torch.cuda.set_rng_state(self.rng_states.pop(0), self.device)
            self.prev_inputs = self.activations.pop(0)

    def update_grads(self):
        return

    def finished_prop(self):
        """reset data after propagation"""

        self.prev_inputs = None

    def backward_mode(self, *inputs: torch.Tensor) -> Tuple[torch.Tensor, ...]:
        """
        function for backward propagation iteration
        """
        # if we have an activation to pass
        if self.counter.output_valid(0):
            output = self.prev_inputs
        else:
            # this iteration is one we should not work in
            output = tensors_map(inputs, torch.empty_like)

        if len(output) == 1:
            output = output[0]

        return output

    def save_activation(self, *moved_inputs: torch.Tensor):
        """
        function for saving layer activations
        """
        self.rng_states.append(torch.cuda.get_rng_state(self.device))
        self.activations.append(tensors_map(
            moved_inputs, lambda input: input.clone()))

    def forward(self, *inputs: torch.Tensor) -> Tuple[torch.Tensor, ...]:
        if self.counter.cur_mode is ForwardMode.backward:
            return self.backward_mode(*inputs)

        moved_inputs = tensors_map(
            inputs, lambda tensor: tensor.to(self.device, non_blocking=True))

        if self.counter.cur_mode is ForwardMode.train and self.counter.input_valid(self.gpu_num):
            with torch.cuda.stream(self.pipe_stream):
                self.save_activation(*moved_inputs)

        if len(moved_inputs) == 1:
            moved_inputs = moved_inputs[0]

        return moved_inputs


class LayerWrapper(nn.Module):
    def __init__(self, module: nn.Module, gpu_num: int, device: str, output_shapes: TensorsShape,
                 counter: CycleCounter = None):
        super(LayerWrapper, self).__init__()

        self.module = module
        self.output_shapes = output_shapes
        self.gpu_num = gpu_num
        self.counter = counter
        self.device = torch.device(device)

    def forward(self, *inputs):
        if self.counter.output_valid(self.gpu_num):
            return self.module(*inputs)

        out = gen_garbage_output(self.output_shapes, self.device)

        if len(out) == 1:
            out = out[0]
        return out

    def __iter(self):
        return iter(self.module)


class Identity(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, x):
        return x


class DeviceDelayerBlock(SyncWrapper):
    def __init__(self, device: str, delay_devices: List[str], gpu_num: int, output_shapes: TensorsShape, counter: CycleCounter = None):
        super().__init__(Identity(), device, gpu_num, output_shapes, counter)

        self.delay_devices = [torch.device(device) for device in delay_devices]

    def delay_device(self, tensor):
        return tensor.device in self.delay_devices


class DeviceDelayer(nn.Module):
    def __init__(self, device: str, device_delays: Dict[str, int], gpu_num: int, output_shapes: TensorsShape, num_inputs=1,
                 counter: CycleCounter = None):
        super().__init__()
        self.device_delays = device_delays.copy()

        self.delay_blocks = []
        num_blocks = max(device_delays.values())

        for idx in range(num_blocks):
            cur_block_devs = [device for device, delay in device_delays.items() if idx < delay]
            self.delay_blocks.append(DeviceDelayerBlock(device, cur_block_devs, gpu_num, output_shapes, counter))

        self.delayer = nn.Sequential(*self.delay_blocks)

    def set_counter(self, counter: CycleCounter):
        for block in self.delay_blocks:
            block.set_counter(counter)

    def forward(self, *inputs):
        return self.delayer(inputs)

    def pop_activation(self):
        for block in self.delay_blocks:
            block.pop_activation()

    def finished_prop(self):
        for block in self.delay_blocks:
            block.finished_prop()

    def update_grads(self):
        for block in self.delay_blocks:
            block.update_grads()

    def __iter__(self):
        return iter(self.delayer)