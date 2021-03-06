import sys
import time

import cPickle as pickle

import lasagne
import numpy as np

import theano
import theano.tensor as T

from nltk.tokenize import word_tokenize

__all__ = ['SkipGram', 'CBOW', 'Corpus']


def profile(func):
    def inner(*args, **kwargs):
        time1 = time.time()
        result = func(*args, **kwargs)
        print('Took %d seconds' % int(time.time() - time1))
        return result
    return inner


def normal(loc=0.0, scale=0.1, size=1, dtype=theano.config.floatX):
    return np.random.normal(loc=loc, scale=scale, size=size).astype(dtype)


class Corpus:
    def __init__(self, data_folder, vocab_size=5000, corpus_file=None):
        import os
        corpus_file_path = '%s.npy' % corpus_file
        self.word_freq = None
        self.word_to_idx = None
        self.sentences = None

        if corpus_file and os.path.isfile(corpus_file_path):
            self.load(corpus_file_path)
            return

        self.parse(data_folder, vocab_size)

        if corpus_file and not os.path.isfile(corpus_file_path):
            self.save(corpus_file_path)

    def parse(self, data_folder, vocab_size):
        import glob
        from collections import Counter

        self.word_freq = Counter()

        file_paths = glob.glob(data_folder + "/*")
        for file_path in file_paths:
            with open(file_path) as f:
                for sentence in f:
                    # Use nltk tokenizer to split the sentence into words
                    words = filter(lambda word: word.isalpha(), word_tokenize(sentence.lower()))
                    for word in words:
                        self.word_freq[word] += 1

        from operator import itemgetter
        top_words = sorted(self.word_freq.items(), key=itemgetter(1), reverse=True)[:vocab_size]

        self.word_freq = Counter()

        # TODO: Remove that
        self.word_to_idx = dict()

        for word, freq in top_words:
            self.word_freq[word] = freq
            self.word_to_idx[word] = len(self.word_to_idx)

        self.word_freq['UNK'] = 0
        self.word_to_idx['UNK'] = len(self.word_to_idx)

        def token_to_idx(token):
            if token in self.word_to_idx:
                return self.word_to_idx[token]
            return len(self.word_to_idx) - 1 # UNK

        self.sentences = []
        for file_path in file_paths:
            with open(file_path) as f:
                for sentence in f:
                    # Use nltk tokenizer to split the sentence into words
                    words = map(token_to_idx, filter(lambda word: word.isalpha(), word_tokenize(sentence.lower())))
                    self.sentences.append(words)

    def vocabs_size(self):
        return len(self.word_freq)

    def load(self, file_name):
        with open(file_name, 'r') as f:
            self.word_freq = pickle.load(f)
            self.word_to_idx = pickle.load(f)
            self.sentences = pickle.load(f)

    def save(self, file_name):
        with open(file_name, 'w') as f:
            pickle.dump(self.word_freq, f)
            pickle.dump(self.word_to_idx, f)
            pickle.dump(self.sentences, f)


class Word2VecBase(object):
    def __init__(self, vector_size, corpus):
        vocabs_size = corpus.vocabs_size()
        self.W_in = theano.shared(value=normal(scale=1./(vocabs_size * vector_size), size=[vocabs_size, vector_size]),
                                  name='W_in', borrow=True)

        self.W_out = theano.shared(value=normal(scale=1./(vocabs_size * vector_size), size=(vector_size, vocabs_size)),
                                   name='W_out', borrow=True)

        nsentences = len(corpus.sentences)
        ten_percent = nsentences // 10
        self.corpus = corpus
        self.train_sentences = self.corpus.sentences[:ten_percent * 9]
        self.valid_sentences = self.corpus.sentences[-ten_percent:]

        self.center_word = T.ivector('center_word')
        self.context = T.imatrix('context')

        self.loss = None

    def load(self, file_name):
        with open(file_name, 'r') as f:
            self.W_in.set_value(pickle.load(f))
            self.W_out.set_value(pickle.load(f))

    def save(self, file_name):
        with open(file_name, 'w') as f:
            pickle.dump(np.asarray(self.W_in.eval()), f)
            pickle.dump(np.asarray(self.W_out.eval()), f)

    def train(self, window_size=5, learning_rate=0.01, epochs=10, batch_size=10,
              update_function=lasagne.updates.adagrad):
        updates = update_function(self.loss, [self.W_in, self.W_out], learning_rate=learning_rate)
        self.train_model = theano.function([self.center_word, self.context], [self.loss], updates=updates)
        self.eval_model = theano.function([self.center_word, self.context], [self.loss])
        print 'Start Training'

        # Padding the beginning and the end of sentences
        unk_idx = len(self.corpus.word_freq) - 1
        padding = [unk_idx] * window_size
        self.train_sentences = map(lambda sentence: padding + sentence + padding, self.train_sentences)
        self.valid_sentences = map(lambda sentence: padding + sentence + padding, self.valid_sentences)

        loss_changes = []
        for epoch in range(epochs):
            np.random.shuffle(self.train_sentences)
            loss, losses = self.train_epoch(window_size, batch_size)
            loss_changes += losses

            print 'Epoch %d, Loss %.6f' % (epoch, loss)
            eval_loss = self.eval_epoch(window_size, batch_size)
            print 'Evaluation Loss %.6f' % eval_loss

        return loss, loss_changes

    @profile
    def eval_epoch(self, window_size, batch_size):
        losses = []
        for batch in range(0, len(self.valid_sentences), batch_size):
            centers = []
            targets = []
            batch_sentences = self.valid_sentences[batch:batch + batch_size]
            for sentence in batch_sentences:
                for idx in range(window_size, len(sentence) - window_size):
                    center_word_idx = sentence[idx]
                    target_word_indexes = sentence[idx - window_size:idx] +\
                                          sentence[idx + 1:idx + window_size + 1]
                    centers.append(center_word_idx)
                    targets.append(target_word_indexes)
            [c_cost] = self.eval_model(centers, targets)
            losses.append(c_cost)
        return np.mean(losses)

    @profile
    def train_epoch(self, window_size, batch_size):
        losses = []

        for batch in range(0, len(self.train_sentences), batch_size):
            centers = []
            targets = []
            batch_sentences = self.train_sentences[batch:batch + batch_size]
            for sentence in batch_sentences:
                for idx in range(window_size, len(sentence) - window_size):
                    center_word_idx = sentence[idx]
                    target_word_indexes = sentence[idx - window_size:idx] +\
                                          sentence[idx + 1:idx + window_size + 1]

                    centers.append(center_word_idx)
                    targets.append(target_word_indexes)
            [c_cost] = self.train_model(centers, targets)
            if batch % 10000 == 0:
                print 'Loss:', c_cost
            losses.append(c_cost)
        return np.mean(losses), losses


class SkipGram(Word2VecBase):
    def __init__(self, vector_size, corpus, lamb=None):
        super(SkipGram, self).__init__(vector_size, corpus)
        # [1, vector_size]
        hidden = T.nnet.relu(self.W_in[self.center_word])

        # [1, vector_size] x [vector_size, vocabs_size] = [1 x vocabs_size]
        Z = T.nnet.logsoftmax(T.dot(hidden, self.W_out))
        self.loss = -T.sum(Z.T[self.context], axis=1)
        self.loss = self.loss.mean()
        if lamb != None:
            self.loss += lamb * lasagne.regularization.l2(self.W_in)


class CBOW(Word2VecBase):
    def __init__(self, vector_size, corpus, lamb=None):
        super(CBOW, self).__init__(vector_size, corpus)

        hidden = T.nnet.relu(T.sum(self.W_in[self.context], axis=0))

        Z = T.nnet.logsoftmax(T.dot(hidden, self.W_out))
        self.loss = -T.sum(Z.T[self.center_word], axis=1)
        self.loss = self.loss.mean()
        if lamb != None:
            self.loss += lamb * lasagne.regularization.l2(self.W_in)

