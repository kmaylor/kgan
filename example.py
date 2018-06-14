from kgan import KGAN
import numpy as np
from tensorflow.examples.tutorials.mnist import input_data

class MNISTGAN(object):
    def __init__(self):
            
        kernels = [4,4,4,4,2]
        strides = [2,2,2,1,1]

        self.img_rows = 28
        self.img_cols = 28
        self.channel = 1

        self.x_train = input_data.read_data_sets("mnist",\
        	one_hot=True).train.images
        self.x_train = self.x_train.reshape(-1, self.img_rows,\
        	self.img_cols, 1).astype(np.float32)

        self.KGAN = KGAN(img_rows=self.img_rows, img_cols=self.img_cols, load_state=True)
        self.KGAN.strides = strides
        self.KGAN.kernels = kernels

    def train(self):
        self.KGAN.train(self.x_train, 'MNIST_sims',train_steps=8000, save_interval=50, verbose = 10)

t = MNISTGAN()
t.train()