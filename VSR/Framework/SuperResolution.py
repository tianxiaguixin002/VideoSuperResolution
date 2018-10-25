"""
Copyright: Wenyi Tang 2017-2018
Author: Wenyi Tang
Email: wenyi.tang@intel.com
Created Date: May 9th 2018
Updated Date: June 15th 2018

Framework for network model (tensorflow)
"""
import tensorflow as tf
import numpy as np
from pathlib import Path

from ..Util.Utility import to_list
from .LayersHelper import Layers
from .Trainer import VSR


class SuperResolution(Layers):
    r"""A utility class helps for building SR architectures easily

    Usage:
        Inherit from `SuperResolution` and implement:
          >>> build_graph()
          >>> build_loss()
          >>> build_summary()
          >>> build_saver()
        If you want to export gragh as a protobuf (say model.pb), implement:
          >>> export_model_pb()
        and call its super method at the end
    """

    def __init__(self, scale, channel, weight_decay=1e-4, **kwargs):
        r"""Common initialize parameters

        Args:
            scale: the scale factor, can be a list of 2 integer to specify different stretch in width and height
            channel: input color channel
            weight_decay: decay of L2 regularization on trainable weights
            rgb_input: if True, specify inputs as RGBA with 4 channels, otherwise the input is grayscale image1
        """

        self.scale = to_list(scale, repeat=2)
        self.channel = channel
        self.weight_decay = weight_decay  # weights regularization
        self.rgba = False  # deprecated
        self._trainer = VSR  # default trainer

        self.inputs = []  # hold placeholder for model inputs
        self.inputs_preproc = []  # hold some image procession for inputs (i.e. RGB->YUV, if you need)
        self.label = []  # hold placeholder for model labels
        self.outputs = []  # hold output tensors
        self.loss = []  # this is the optimize op
        self.train_metric = {}  # metrics show at training phase
        self.metrics = {}  # metrics record in tf.summary and show at benchmark
        self.feed_dict = {}
        self.savers = {}
        self.global_steps = None
        self.training_phase = None  # only useful for bn
        self.learning_rate = None
        self.summary_op = None
        self.summary_writer = None
        self.compiled = False
        self.unknown_args = kwargs

    def __getattr__(self, item):
        """return extra initialized parameters"""
        if item in self.unknown_args:
            return self.unknown_args.get(item)
        return super(SuperResolution, self).__getattr__(item)

    @property
    def trainer(self):
        return self._trainer

    def compile(self):
        """build entire graph and training ops"""

        self.global_steps = tf.Variable(0, trainable=False, name='global_step')
        self.training_phase = tf.placeholder(tf.bool, name='is_training')
        self.learning_rate = tf.placeholder(tf.float32, name='learning_rate')
        self.build_graph()
        self.build_loss()
        self.build_summary()
        self.summary_op = tf.summary.merge_all()
        self.build_saver()
        self.compiled = True
        return self

    def display(self):
        """print model information"""

        pass

    def build_saver(self):
        """Build variable savers.

        By default, I build a saver to save all variables. In case you need to recover a part of variables,
        you can inherit this method and create multiple savers for different variables. All savers should
        arrange in a dict which maps saver and its saving name
        """

        default_saver = tf.train.Saver(max_to_keep=3, allow_empty=True)
        self.savers = {self.name: default_saver}

    def build_graph(self):
        """this super method create input and label placeholder

        Note
            You can also suppress this method and create your own inputs from scratch
        """
        self.inputs.append(tf.placeholder(tf.uint8, shape=[None, None, None, None], name='input/lr'))
        inputs_f = tf.to_float(self.inputs[0])
        # separate additional channels (e.g. alpha channel)
        self.inputs_preproc.append(inputs_f[..., self.channel:])
        self.inputs_preproc.append(inputs_f[..., :self.channel])
        self.inputs_preproc[-1].set_shape([None, None, None, self.channel])
        self.label.append(tf.placeholder(tf.float32, shape=[None, None, None, self.channel], name='label/hr'))

    def build_loss(self):
        """help to build mse loss via self.label[-1] and self.outputs[-1] for simplicity

        >>> loss = tf.losses.mean_squared_error(self.label[-1], self.outputs[-1])

        Note
            You can also suppress this method and build your own loss function from scratch
        """

        opt = tf.train.AdamOptimizer(self.learning_rate)
        mse = tf.losses.mean_squared_error(self.label[-1], self.outputs[-1])
        loss = tf.losses.get_total_loss()
        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        with tf.control_dependencies(update_ops):
            self.loss.append(opt.minimize(loss, self.global_steps))

        return mse, loss

    def build_summary(self):
        # the pure abstract method
        raise NotImplementedError('DO NOT use base SuperResolution directly! Use inheritive models instead.')

    def train_batch(self, feature, label, learning_rate=1e-4, **kwargs):
        r"""training one batch one step

        Args:
            feature: input tensors, LR image1 for SR use case
            label: labels, HR image1 for SR use case
            learning_rate: update step size in current calculation
            kwargs: for future use

        Return:
            the results of ops in `self.loss`
        """

        feature = to_list(feature)
        label = to_list(label)
        self.feed_dict.update({self.training_phase: True, self.learning_rate: learning_rate})
        for i in range(len(self.inputs)):
            self.feed_dict[self.inputs[i]] = feature[i]
        for i in range(len(self.label)):
            self.feed_dict[self.label[i]] = label[i]
        loss = kwargs.get('loss') or self.loss
        loss = to_list(loss)
        loss = tf.get_default_session().run(list(self.train_metric.values()) + loss, feed_dict=self.feed_dict)
        ret = {}
        for k, v in zip(self.train_metric, loss):
            ret[k] = v
        return ret

    def test_batch(self, inputs, label=None, **kwargs):
        r"""test one batch

        Args:
            inputs: LR images
            label: if None, return only predicted outputs; else return outputs along with metrics
            kwargs: for future use

        Return:
            predicted outputs, metrics if `label` is not None
        """

        feature = to_list(inputs)
        label = to_list(label)
        self.feed_dict.update({self.training_phase: False})
        for i in range(len(self.inputs)):
            self.feed_dict[self.inputs[i]] = feature[i]
        if label:
            for i in range(len(self.label)):
                self.feed_dict[self.label[i]] = label[i]
            results = tf.get_default_session().run(self.outputs + list(self.metrics.values()),
                                                   feed_dict=self.feed_dict)
            outputs, metrics = results[:len(self.outputs)], results[len(self.outputs):]
        else:
            results = tf.get_default_session().run(self.outputs, feed_dict=self.feed_dict)
            outputs, metrics = results, []
        ret = {}
        for k, v in zip(self.metrics, metrics):
            ret[k] = v
        return outputs, ret

    def summary(self):
        return tf.get_default_session().run(self.summary_op, feed_dict=self.feed_dict)

    def export_model_pb(self, export_dir='.', export_name='model.pb', **kwargs):
        r"""export model as a constant protobuf. Unlike saved model, this one is not trainable

        Args:
            export_dir: directory to save the exported model
            export_name: model name
        """

        self.outputs = tf.identity_n(self.outputs, name='output/hr')
        sess = tf.get_default_session()
        graph = sess.graph.as_graph_def()
        graph = tf.graph_util.remove_training_nodes(graph)
        graph = tf.graph_util.convert_variables_to_constants(
            sess, graph, [outp.name.split(':')[0] for outp in self.outputs])
        tf.train.write_graph(graph, export_dir, export_name, as_text=False)
        tf.logging.info(f"Model exported to [ {Path(export_dir).resolve() / export_name} ].")

    def export_saved_model(self, export_dir='.'):
        """export a saved model

        Args:
            export_dir: directory to save the saved model
        """

        sess = tf.get_default_session()
        builder = tf.saved_model.builder.SavedModelBuilder(export_dir)
        tf.identity_n(self.outputs, name='output/hr')
        builder.add_meta_graph_and_variables(sess, tf.saved_model.tag_constants.SERVING)
        builder.save()


class SuperResolutionDisc(SuperResolution):
    """SuperResolution with Discriminator.

    Bind some common discriminators for GAN + SR training
    """

    @staticmethod
    def _view(inputs, input_shape):
        input_shape = np.asarray(input_shape, dtype='int32').tolist()
        input_shape = list(input_shape)
        if len(input_shape) == 3:
            input_shape.insert(0, -1)
        if len(input_shape) != 4:
            raise ValueError('invalid shape (HWC or BHWC) for discriminator: ' + str(input_shape))
        if input_shape[1] and input_shape[2]:
            if input_shape[0] is None:
                input_shape[0] = -1
            x = tf.reshape(inputs, input_shape)
            has_shape = True
        else:
            has_shape = False
            x = tf.identity(inputs)
        return x, has_shape

    def standard_d(self, input_shape, filters, depth, dup_layer=False,
                   activation='lrelu', bias=True, norm=None, name='SDisc'):
        """Standard Discriminator

        Args:
            input_shape: a tuple of 3 or 4 integers, [H, W, C] or [B, H, W, C], where B can be None
            filters: an integer representing initial filter numbers
            depth: an integer representing layer depth of the discriminator
            dup_layer: a boolean, whether duplicate each layer with strides=1
            activation: override activation function of every layer
            bias: a boolean, whether add bias to each layer
            norm: a string, representing normalization method (BN or SN for now)
            name: a string specify scope of the discriminator, if None, the default scope is 'SDisc'

        Return:
            a callable with reuse flag
        """
        bn = np.any([word in norm for word in ('bn', 'batch')])
        sn = np.any([word in norm for word in ('sn', 'spectral')])

        def critic(inputs, conditions=None):
            with tf.variable_scope(name, reuse=tf.AUTO_REUSE):
                x, has_shape = self._view(inputs, input_shape)
                f = filters
                for _ in range(depth):
                    if dup_layer:
                        x = self.conv2d(x, f, 3, use_batchnorm=bn, use_sn=sn, use_bias=bias, activation=activation)
                    x = self.conv2d(x, f, 4, 2, use_batchnorm=bn, use_sn=sn, use_bias=bias, activation=activation)
                    f *= 2
                if has_shape:
                    x = tf.layers.flatten(x)
                    x = tf.layers.dense(x, 1024, activation=self._act(activation), use_bias=bias)
                    x = tf.layers.dense(x, 1, use_bias=bias)
                else:
                    x = self.conv2d(x, 1, 3, use_bias=bias)
                    x = tf.reduce_mean(x, [1, 2, 3])
                return x

        return critic

    def project_d(self, input_shape, filters, depth, dup_layer=False, extract_layer=None,
                  activation='lrelu', bias=True, norm=None, name='ProjDisc'):
        """Projection Discriminator

        Args:
            input_shape: a tuple of 3 or 4 integers, [H, W, C] or [B, H, W, C], where B can be None
            filters: an integer representing initial filter numbers
            depth: an integer representing layer depth of the discriminator
            dup_layer: a boolean, whether duplicate each layer with strides=1
            extract_layer: an integer or None, combine which layer's output with linear output
            activation: override activation function of every layer
            bias: a boolean, whether add bias to each layer
            norm: a string, representing normalization method (BN or SN for now)
            name: a string specify scope of the discriminator, if None, the default scope is 'ProjDisc'

        Return:
            a callable with reuse flag
        """
        bn = np.any([word in norm for word in ('bn', 'batch')])
        sn = np.any([word in norm for word in ('sn', 'spectral')])

        def critic(inputs, conditions=None):
            with tf.variable_scope(name, reuse=tf.AUTO_REUSE):
                x, has_shape = self._view(inputs, input_shape)
                if not has_shape:
                    raise ValueError('Input shape must be specified!')
                f = filters
                x = self.conv2d(x, f, 3, activation=activation, use_sn=sn, use_batchnorm=bn, use_bias=bias)
                for i in range(depth):
                    x = self.resblock(x, f, 3, activation=activation, use_bias=bias, placement='front',
                                      use_sn=sn, use_batchnorm=bn)
                    x = tf.layers.average_pooling2d(x, 2, 2)
                    if dup_layer:
                        x = self.resblock(x, f, 3, activation=activation, use_bias=bias, placement='front',
                                          use_sn=sn, use_batchnorm=bn)
                    if extract_layer == i + 1:
                        phi = x
                    f *= 2
                x = tf.layers.flatten(x)
                x = tf.layers.dense(x, 1024, activation=self._act(activation), use_bias=bias)
                x = tf.layers.dense(x, 1, use_bias=bias)
                if conditions is not None and extract_layer:
                    phi = self.conv2d(phi, self.channel, 3, use_sn=sn, use_batchnorm=bn, use_bias=bias)
                    phi = tf.layers.flatten(phi)
                    phi = tf.matmul(phi, tf.layers.flatten(conditions), transpose_b=True)
                    return x + phi
                return x

        return critic
