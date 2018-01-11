import gym

import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.utils.data import Dataset, DataLoader
from torch.utils.data.sampler import Sampler

from .space import TensorBox
from .util import UniformSampler


class PytorchAdversarialEnv(gym.Env):
    """
    An environment for generating and defending against adversarial examples with PyTorch models
        and Datasets.

    Args:
        target_model (subclass of torch.nn.Module): The model we're attacking.
            Currently, only torch.nn.Module is supported.
        dataset (subclass of torch.utils.data.Dataset): The dataset we're using to attack.
            Currently, only Pytorch Dataset objects are supported.
            Note: ToTensor transform must be included.
        norm (float, optional): P value of L-p norm to use for contrained penalty on reward.
            Only called in reward wrappers. If None, no norm penalty is taken.
        batch_size (int, optional): Number of instances for the target_model to classify per step.
        episode_length (positive integer, optional): Specifies the number of steps to include in
            each episode.  Default is len(dataset)//batch_size.
        sampler (subclass of torch.utils.data.Sampler, optional): Specifies the sampling strategy.
            Default is uniform random sampling with replacement over the entire dataset.
        num_workers (integer, optional): Argument to be passed to DataLoader specifying number of
            subprocess threads to use for data loading.
        use_cuda: (bool, optional): Whether to place tensors and model on GPU.
            Defaults to True if GPU is available.
        seed: (int, optional): integer to use for random seed.  If None, use default Pytorch RNG.
            Note: setting a seed does not guarantee determinism when using CUDNN backend.
            For this reason, we disable CUDNN if a seed is specified.

    """
    def __init__(self, target_model, dataset, norm = None, batch_size = 1, episode_length = None,
            sampler = None, num_workers = 0, use_cuda = torch.cuda.is_available(), seed = None):
        super(PytorchAdversarialEnv).__init__()
        self.use_cuda = use_cuda
        if seed is not None:
            torch.backend.cudnn.enabled = False
        self.seedey = self._seed(seed)
        self.target_model = target_model.cuda() if use_cuda else target_model.cpu()
        self.dataset = dataset
        space_shape = self.dataset[0][0].size()
        space_shape = (batch_size, *space_shape[1:])
        self.action_space = TensorBox(0, 1, space_shape)
        self.observation_space = TensorBox(0, 1, space_shape)
        self.episode_length = len(self.dataset)//batch_size if not episode_length else episode_length
        self.sampler = UniformSampler(self.dataset, self.torch_rng, len(self.dataset)) if not sampler else sampler

        if not self._check_dataset():
            raise gym.error.Error('Dataset type {} not supported.'.format(type(self.dataset)) +
                              'Currently, dataset must be a subclass of torch.utils.data.Dataset containing FloatTensors')

        if not self._check_model():
            raise gym.error.Error('Model type {} not supported.'.format(type(self.target_model)) +
                              ' Currently, target_model must be a subclass of torch.nn.Module.')

        if not self._check_sampler():
            raise gym.error.Error('Sampler type {} not supported.'.format(type(self.sampler)) +
                               'Currently, sampler must be a subclass of torch.utils.data.sampler.Sampler.')

        self.batch_size = batch_size
        self.norm = norm
        self.num_workers = num_workers
        self.data_loader = DataLoader(self.dataset, batch_size = self.batch_size, sampler = self.sampler, num_workers = self.num_workers)
        self.iterator = iter(self.data_loader)
        self._reset()

    def _step(self, action, **kwargs):
        try:
            current_obs = self.successor
            self.successor = self.iterator.__next__()
            self.ix += 1
            if self.ix >= self.episode_length:
                raise StopIteration
        except StopIteration:
            self.done = True
        if self.use_cuda:
            action = action.cuda()
            self.successor[0] = self.successor[0].cuda()
            self.successor[1] = self.successor[1].cuda()
        else:
            action = action.cpu()
        reward, info = self._get_reward(current_obs, action, **kwargs)
        return self.successor, reward, self.done, info

    def _seed(self, seed):
        integer_types = (int,)
        if seed is not None and not (isinstance(seed, integer_types) and 0 <= seed):
            raise gym.error.Error('Seed must be a non-negative integer or omitted, not {}.'.format(type(seed)))
        self.torch_rng = torch.manual_seed(seed) if seed is not None else torch.default_generator
        self.seedey = seed
        return [seed]

    def _reset(self):
        self.data_loader = DataLoader(self.dataset, batch_size = self.batch_size, sampler = self.sampler, num_workers = self.num_workers)
        self.iterator = iter(self.data_loader)
        self.successor = self.iterator.__next__()
        if self.use_cuda:
            self.successor[0] = self.successor[0].cuda()
            self.successor[1] = self.successor[1].cuda()
        self.done = False
        self.ix = 0
        return self.successor

    def _get_reward(self, obs, action, **kwargs): raise NotImplementedError

    def norm_on_batch(self, input, p):
        # Assume dimension 0 is batch dimension
        norm_penalty = input
        while len(norm_penalty.size())>1:
            norm_penalty = torch.norm(norm_penalty, p, -1)
        return norm_penalty

    def _check_model(self):
        return isinstance(self.target_model, nn.Module)

    def _check_dataset(self):
        return isinstance(self.dataset, Dataset) and (isinstance(self.dataset[0][0], torch.FloatTensor) or isinstance(self.dataset[0][0], torch.cuda.FloatTensor))

    def _check_sampler(self):
        return isinstance(self.sampler, Sampler)