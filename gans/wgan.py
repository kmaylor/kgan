import sys
sys.path.append('..')
import numpy as np
import os
import types
from keras import __version__
print("Using Keras version = "+__version__)
from keras.models import Sequential
from keras.layers import Dense, Activation, Flatten, Reshape
from keras.layers import Conv2D, Cropping2D, UpSampling2D
from keras.layers import LeakyReLU, Dropout, Lambda, ReLU
from keras.layers import BatchNormalization
from keras.optimizers import RMSprop
from keras.utils import multi_gpu_model
from keras import backend as K
import tensorflow as tf
from keras_layer_normalization import LayerNormalization
from utils.utils import *

class WGAN(object):
    """ Class for quickly building a WGAN model (Arjovsky et al. https://arxiv.org/pdf/1701.07875.pdf)
    
    # Arguments
        img_dims: Number of rows, columns, and channels in an image from the training set.
            Channels is 1 for grayscale, 3 for rgb.
        kernels: A list of ints containing the kernel size to be used for each convolution in
            the discriminator, assumed symmetric. The reversed list is used for the generator kernels.
        strides: A list of ints containing the stride or scaling to be used in each convolution.
        min_depth: The number of features created at the first convolution in the discriminator, or
            the number of features created at the second to last convolution in the generator. 
        depth_scale: Function or tuple of ints to deterimine the number of features created at each convolution
            after the first convolution in the discriminator. Default is to increase by a factor of 2 after each
            convolution.
        latent_dim: Number of dimensions of the latent vector. Drawn from normal disctribution.
        load_dir: Directory containing saved models used as starting point.
        save_dir: Directory to save GAN models in.
        clip_value: Maximum weight value for discriminator.
        gpus: Number of gpus to use for training.
        discriminator_optimizer: Optimizer to use for discriminator training. Default is 
            Adam(lr=0.0002,beta_1=0.5, decay=0)
        generator_optimizer: Optimizer to use for generator training. Default is 
            Adam(lr=0.0002,beta_1=0.5, decay=0)
    """
    def __init__(self, 
                    img_dims,
                    kernels,
                    strides,
                    min_depth,
                    depth_scale = None,
                    latent_dim = 100,
                    load_dir =None,
                    save_dir = 'Saved_Models/wgan',
                    clip_value = 0.01,
                    gpus=1,
                    discriminator_optimizer = RMSprop(lr=0.0001),
                    generator_optimizer = RMSprop(lr=0.00005),
                    ):
        
        self.img_rows, self.img_cols, self.channel = img_dims
        self.clip_value = clip_value
        self.gpus = gpus
        self.save_dir = save_dir
        self.load_dir = load_dir
        self.latent_dim = latent_dim
        self.kernels = kernels
        self.strides = strides
        self.depth = min_depth
        self.discriminator_optimizer = discriminator_optimizer
        self.generator_optimizer = generator_optimizer
        
        if depth_scale == None:
            self.depth_scale = lambda:((2*np.ones(len(self.kernels)))**np.arange(len(self.kernels))).astype('int')
        
        self.models = dict.fromkeys(['discriminator','generator','discriminator_model','adversarial_model'])
        
        if load_dir != None:
            print('Loading Previous State')
            load_state(self)
        else:
            self.models['discriminator'] = self.build_discriminator()   # discriminator
            self.models['generator'] = self.build_generator()   # generator
        self.models['discriminator_model'] = self.build_discriminator_model()  # discriminator model
        self.models['adversarial_model'] = self.build_adversarial_model()  # adversarial model

    def build_discriminator(self):
        '''Create the discriminator'''
        
        def discriminator_block(D, depth, kernel_size, stride):
            D.add(Conv2D(depth, kernel_size, strides=stride, padding='same', \
                        kernel_initializer='he_normal',bias_initializer='zeros', name = 'Conv2D_D%i'%(i+2)))
            D.add(LayerNormalization( name = 'BN_D%i'%(i+2)))
            D.add(LeakyReLU(alpha=0.2, name = 'LRelu_D%i'%(i+2)))
        
        # depth*scale_depth give the number of features for each layer
        depth = self.depth
        if isinstance(self.depth_scale,types.FunctionType):
            depth_scale = self.depth_scale()
        else:
            depth_scale = self.depth_scale
        
        input_shape = (self.img_rows, self.img_cols, self.channel)

        # Discriminator is sequential model
        D = Sequential(name='Discriminator')
        # First layer of discriminator i a convolution, minimum of two convolutional layers
        D.add(Conv2D(depth*depth_scale[0], self.kernels[0], strides=self.strides[0], \
                    input_shape=input_shape, padding='same', kernel_initializer='he_normal',
                    bias_initializer='zeros', name = 'Conv2D_D1'))
        D.add(LeakyReLU(alpha=0.2, name = 'LRelu_D1'))
        
        # Iterate over layers defined by the number of kernels and strides
        for i,ks in enumerate(zip(self.kernels[1:],self.strides[1:])):
            discriminator_block(D,depth*depth_scale[i+1],ks[0],ks[1])
        
        # Flatten final features and calculate the probability of the input belonging to the same 
        # as the training set
        D.add(Flatten(name = 'Flatten'))
        # D.add(Dense(1024, kernel_initializer='he_normal',bias_initializer='zeros', name = 'Dense_D1'))
        # D.add(LeakyReLU(alpha=0.2, name = 'LRelu_D%i'%(i+3)))
        D.add(Dense(1, kernel_initializer='glorot_normal',bias_initializer='zeros', name = 'Dense_D2'))
        
        D.summary()
        return D

    def build_generator(self):
        '''Create the generator'''
        
        def generator_block(G,depth,kernel,stride):
            G.add(UpSampling2D(stride,name='UpSample_%i'%(i+1), interpolation='bilinear'))
            G.add(Conv2D(depth, kernel, strides = 1, padding='same',
                    kernel_initializer='he_normal',bias_initializer='zeros', name = 'Conv2D_G%i'%(i+1)))
            G.add(LayerNormalization( name = 'BN_G%i'%(i+2)))
            G.add(LeakyReLU(alpha=0.2, name = 'LRelu_G%i'%(i+2)))
        
        
        # depth/2*scale_depth give the number of features for each layer
        depth = self.depth
        if isinstance(self.depth_scale,types.FunctionType):
            depth_scale = self.depth_scale()[::-1]
        else:
            depth_scale = self.depth_scale[::-1]
        
        #Get the size of the initial features from the size of the final feature layer in 
        #the discriminator
        dim1 = self.models['discriminator'].get_layer('Flatten').input_shape[1]
        dim2 = self.models['discriminator'].get_layer('Flatten').input_shape[2]
        
        # Generator is sequential model
        G = Sequential(name='Generator')
        
        # First layer of generator is densely connected
        G.add(Dense(dim1*dim2*depth*depth_scale[0], input_dim=self.latent_dim,
                        kernel_initializer='he_normal',bias_initializer='zeros', name = 'Dense_G'))
        G.add(Reshape((dim1, dim2, depth*depth_scale[0]),name='Reshape'))
        G.add(LayerNormalization( name = 'BN_G1'))
        G.add(LeakyReLU(alpha=0.2, name = 'LRelu_G1'))

        # Iterate over layers defined by the number of kernels and strides
        #Use larger kernels with larger feature maps
        for i,ks in enumerate(zip(self.kernels[:-1],self.strides[:-1])):
            generator_block(G,depth*depth_scale[i+1],ks[0],ks[1])
        
        G.add(UpSampling2D(self.strides[-1],name='UpSample_%i'%(i+2), interpolation='bilinear'))
        G.add(Conv2D(self.channel, self.kernels[-1], strides = 1, padding='same',
                kernel_initializer='glorot_normal',bias_initializer='zeros', name = 'Conv2D_G%i'%(i+2)))
        G.add(Activation('tanh', name = 'Tanh'))
        
        # If the output of the last layer is larger than the input for the discriminator crop
        # the image
        crop_r = int((G.get_layer('Tanh').output_shape[1]-self.img_rows)/2)
        crop_c = int((G.get_layer('Tanh').output_shape[2]-self.img_cols)/2)
        G.add(Cropping2D(cropping=((crop_c,crop_c),(crop_r,crop_r)), name = 'Crop2D'))
        
        G.summary()
        return G

    def wasserstein_loss(self, y_true, y_pred):
        return K.mean(y_true * y_pred)
    
    def build_discriminator_model(self):
        '''Build and compile the discriminator model from the discriminator.'''    

        # Compile the discriminator model on the number of specified gpus
        if self.gpus <=1:
            DM = Sequential(name = 'Discriminator Model')
            DM.add(self.models['discriminator'])
        else:
            with tf.device("/cpu:0"):
                DM = Sequential(name = 'Discriminator_Model')
                DM.add(self.models['discriminator'])
            DM = multi_gpu_model(DM,gpus=self.gpus)
        DM.compile(loss=self.wasserstein_loss, optimizer=self.discriminator_optimizer)

        DM.summary()
        return DM

    def build_adversarial_model(self):
        '''Build and compile the adversarial model from the discriminator and generator. Stacks the 
        discriminator on the generator'''
        
        # Only use the discriminator to evaluate the generator's output
        self.models['discriminator'].trainable = False
        # Compile the gemerator model on the number of specified gpus
        if self.gpus <=1:
            AM = Sequential(name = 'Adversarial Model')
            AM.add(self.models['generator'])
            AM.add(self.models['discriminator'])
        else:
            with tf.device("/cpu:0"):
                AM = Sequential(name = 'Adversarial_Model')
                AM.add(self.models['generator'])
                AM.add(self.models['discriminator'])
            AM = multi_gpu_model(AM,gpus=self.gpus)
        AM.compile(loss=self.wasserstein_loss, optimizer=self.generator_optimizer)
        
        # Set the discriminator back to trainable so the discriminator is in the correct state when reloading
        # a model
        self.models['discriminator'].trainable = True
        AM.summary()
        return AM

    
    
    def train(self,
                x_train,
                fileprefix,
                train_rate=(5,1),
                train_steps=2000,
                batch_size=32,
                save_rate=100,
                mesg_rate = 10,
                samples=16,
                nan_threshold = 100,
                call_back = None):
        '''Trains the generator and discriminator.
        
        # Arguments
            x_train: Data to train models on.
            fileprefix: Path to where to save the sample images and log files.
            train_rate: Iterable containing number of times to train the discriminator
                and the generator in that order.
            train_steps: Number of batches to train on before stopping.
            batch_size: Number of samples to train discriminator and generator on each step.
            save_rate: Number of steps afterwhich the models and samples will be saved.
            mesg_rate: Number of steps afterwhich the models loss will be printed.
            samples: Number of images in output plot.
            nan_threshold: Number of allowed consecutive times the total loss for all models
                can be NaN before stopping training.
            call_back: A function that will be called at the same rate as mesg_rate and takes the
                current DCGAN instance as input.
        '''
        logger = ProgressLogger(fileprefix, mesg_rate = mesg_rate,
                                save_rate = save_rate, nan_threshold = nan_threshold, call_back=call_back)
        
        # Function used for clipping the weights in the discriminator model.
        def weight_clipper(model):
            for l in model.layers:
                weights = l.get_weights()
                weights = [np.clip(w, -self.clip_value, self.clip_value) for w in weights]
                l.set_weights(weights)
        
        print('Training Beginning')
        
        y_real = -np.ones((batch_size, 1))
        y_fake = np.ones((batch_size, 1))
            
        for i in range(train_steps):
            for k in range(train_rate[0]):
                # First train the discriminator with correct labels
                # Randomly select batch from training samples
                images_real = x_train[np.random.randint(0,
                    x_train.shape[0], size=batch_size), :, :, :]
                
                # Generate fake images from generator
                noise = np.random.normal(loc=0., scale=1., size=[batch_size, self.latent_dim])
                images_fake = self.models['generator'].predict(noise)
                
                d_loss_real = self.models['discriminator_model'].train_on_batch(images_real, y_real)
                d_loss_fake = self.models['discriminator_model'].train_on_batch(images_fake,y_fake)
                
                weight_clipper(self.models['discriminator_model'])
                    
            #d_loss = np.add(d_loss_fake,d_loss_real)*0.5
            
            # Now train the adversarial network
            # Create new fake images labels as if they are from the training set
            
            for j in range(train_rate[1]):
                noise = np.random.normal(loc=0., scale=1., size=[batch_size, self.latent_dim])
                a_loss = self.models['adversarial_model'].train_on_batch(noise, y_real)
            
            #Log losses and generator plots
            tracked = {'Discriminator Real loss':d_loss_real,
                       'Discriminator Generated loss':d_loss_fake,
                       'Average Discriminator loss': (d_loss_real+d_loss_fake)/2,
                       'Generator loss': a_loss}
            logger.update(self, tracked, x_samples = images_real[:8])
            
    #def train_on_generator():

    

    
            
        
