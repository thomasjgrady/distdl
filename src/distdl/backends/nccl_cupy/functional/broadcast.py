__all__ = ["BroadcastFunction"]

import cupy as cp
import torch
from mpi4py import MPI

from distdl.utilities.torch import zero_volume_tensor


# A better idea is to implement a progress engine for this purpose
def reduce_function(partition, src, dst):
    partition._comm.Reduce(src, dst, root=0, op=MPI.SUM)


class BroadcastFunction(torch.autograd.Function):
    r"""MPI-based functional implementation of a distributed broadcast layer.

    Implements the required `forward()` and adjoint (`backward()`) operations
    for a distributed Broadcast layer using the PyTorch autograd interface.

    This implementation uses MPI for data movement, accessed through the
    ``mpi4py`` MPI wrappers.

    Warning
    -------
    This implementation currently requires that tensors have data stored in main
    memory (CPU) only, not auxiliary memories such as those on GPUs.

    Warning
    -------
    The ``mpi4py`` interface currently used requires NumPy views of the tensors.

    """

    @staticmethod
    def forward(ctx, input, P_send, P_recv, preserve_batch,
                input_tensor_structure, output_tensor_structure,
                scale_backward):
        r"""Forward function of distributed broadcast layer.

        This method implements the forward broadcast operation using the
        ``MPI_Ibcast`` function.

        Any given worker may participate in two MPI broadcasts, one on the
        ``P_send`` partition and one on the ``P_recv`` partition.  The
        communication pattern and function selection is to avoid potential
        deadlocks due to potential overlaps in these partitions.

        When the current worker is active in its ``P_send`` partition, it
        *always* has data that it must share.  It will only be active in
        ``P_send`` if it is the root worker of that partition, therefore, it
        will send tensor data as the root of an ``MPI_Ibcast``.

        When the current worker is active in its ``P_recv`` partition, there are
        multiple potential scenerios.

        1. If it is *active* in a ``P_send`` partition and ``P_send`` is the
           *same* partition as ``P_recv``, then the input subtensor can simply
           be cloned for the output.
        2. If it is *active* in a ``P_send`` partition and ``P_send`` is a
           *different* partition from ``P_recv``, then it will receive tensor
           data from the root of an ``MPI_Ibcast``.
        3. If it is *inactive* in a ``P_send`` partition, then it will receive
           tensor data from the root of an ``MPI_Ibcast``.

        When the current worker is inactive in the ``P_recv`` partition, it will
        output a zero-volume tensor, potentially preserving a non-zero batch
        size.

        Parameters
        ----------
        ctx :
            PyTorch context.
        input : `torch.tensor`
            Input tensor.
        P_send : Partition
            Sending partition current worker is a part of.
        P_recv : Partition
            Receiving partition current worker is a part of.
        preserve_batch : bool
            Indicates if batch size should be preserved for zero-volume outputs.
        input_tensor_structure : tuple
            Tuple containing properties of the input tensor (dimension, shape,
            requires_grad).
        output_tensor_structure : tuple
            Tuple containing properties of the output tensor (dimension, shape,
            requires_grad).
        scale_backward : int
            Divide the backward pass by this number.

        Returns
        -------
        output :
            Output tensor.

        """
        device = input.device
        ctx.P_send = P_send
        ctx.P_recv = P_recv
        ctx.preserve_batch = preserve_batch
        ctx.input_tensor_structure = input_tensor_structure
        ctx.output_tensor_structure = output_tensor_structure
        ctx.scale_backward = scale_backward
        ctx.device = device

        # This allows all ranks to use the same exit path, so that we can be
        # sure that all requests have cleared.
        if preserve_batch:
            output = zero_volume_tensor(input.shape[0], device=device, dtype=output_tensor_structure.dtype)
        else:
            output = zero_volume_tensor(device=device, dtype=output_tensor_structure.dtype)

        # Send all of the data
        if P_send.active:
            stream = cp.cuda.stream.get_current_stream()
            P_send._nccl.broadcast(input.detach().contiguous(), root=0, stream=stream)

        if P_recv.active:
            # If I send to and receive from the same partition, make a copy.
            if P_send == P_recv:
                output = input
            # If I just receive, receive the broadcast
            else:
                output = torch.zeros(output_tensor_structure.shape, dtype=output_tensor_structure.dtype, device=device)
                stream = cp.cuda.stream.get_current_stream()
                P_recv._nccl.broadcast(output, root=0, stream=stream)
                output.requires_grad_(output_tensor_structure.requires_grad)

        return output

    @staticmethod
    def backward(ctx, grad_output):
        r"""Backward function of distributed broadcast layer.

        This method implements the adjoint of the Jacobian of the broadcast
        operation, a sum-reduce, using the ``MPI_Ireduce`` function.

        The roles of the respective send and receive partitions are reversed in
        the adjoint algorithm.  Any worker that was the source of copied data
        in the forward algorithm will be the destination of reduced data in
        the adjoint.

        Any given worker may participate in two MPI reductions, one on the
        ``P_recv`` partition and one on the ``P_send`` partition.  The
        communication pattern and function selection is to avoid potential
        deadlocks due to potential overlaps in these partitions.

        When the current worker is active in its ``P_recv`` partition, it
        *always* has data that must be reduced.  Therefore it will always send
        data (through a sum-reduce) to the root of that partition.

        If the current worker is active in ``P_send`` then it is guaranteed to be the root
        worker of ``P_send`` and there are two potential scenerios.

        1. If the ``P_send`` and ``P_recv`` partitions are distinct, the
           current worker will receive reduced tensor data as the root of an
           additional ``MPI_Ireduce``.
        2. If the ``P_send`` and ``P_recv`` partitions are the same, the
           reduction is completed by the *first* ``MPI_Ireduce`` and the second
           is not necessary, and in fact will cause a deadlock.

        When the current worker is inactive in the ``P_send`` partition, it will
        output a zero-volume tensor, potentially preserving a non-zero batch
        size.

        Parameters
        ----------
        ctx :
            PyTorch context.
        grad_output : `torch.tensor`
            Input tensor.

        Returns
        -------
        grad_input :
            Output tensor.
        """

        P_send = ctx.P_send
        P_recv = ctx.P_recv
        preserve_batch = ctx.preserve_batch
        input_tensor_structure = ctx.input_tensor_structure
        output_tensor_structure = ctx.output_tensor_structure
        device = ctx.device

        assert grad_output.device == device

        # This allows all ranks to use the same exit path, so that we can be
        # sure that all requests have cleared.
        if preserve_batch:
            grad_input = zero_volume_tensor(grad_output.shape[0], device=device, dtype=output_tensor_structure.dtype)
        else:
            grad_input = zero_volume_tensor(device=device, dtype=output_tensor_structure.dtype)

        # If I received data (either from a remote worker or just from myself)
        # I need to reduce that data.  If I send and receive to myself, this
        # is OK, as the reduction accounts for the copy, unlike the broadcast
        # above.

        if P_recv.active:
            if ctx.scale_backward is not None:
                grad_output.div_(ctx.scale_backward)
            reduced_data_recv = torch.zeros(output_tensor_structure.shape, dtype=output_tensor_structure.dtype,
                                            device=device)
            stream = cp.cuda.stream.get_current_stream()
            P_recv._nccl.reduce(grad_output.detach().contiguous(), reduced_data_recv, op='sum', root=0, stream=stream)

        # If I sent data in the forward, I have to receive it here.  Unless I
        # also received that data, then I already have it from above.
        if P_send != P_recv and P_send.active:
            reduced_data_send = torch.zeros(input_tensor_structure.shape, dtype=input_tensor_structure.dtype,
                                            device=device)
            stream = cp.cuda.stream.get_current_stream()
            P_send._nccl.reduce(reduced_data_send, reduced_data_send, op='sum', root=0, stream=stream)

        # If we had to receive data, we need to tensorify it.
        if P_send.active:
            if P_send == P_recv:
                grad_input = reduced_data_recv
                grad_input.requires_grad_(input_tensor_structure.requires_grad)
            else:
                grad_input = reduced_data_send
                grad_input.requires_grad_(input_tensor_structure.requires_grad)

        return grad_input, None, None, None, None, None, None
