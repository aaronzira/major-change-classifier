"""Major/Minor Change Classifier

Classifier using trained SVC files in "./SVC.pkl" and dependent "*.npy"
files used to differentiate major and minor errors between two versions of
the same transcription. Uses three metrics: Word Mover's Distance, ratio
of difference in index of words between strings (index is a proxy for word
rarity, and is from the word2vec embeddings trained on 3B words from
Google News), and the ratio of string similarity using Levenshtein
distance. Out-of-vocabulary (OOV) words are removed before making
calcualtions, after a number of processing steps to remove filler words
and transcription notes, as well as convert numbers (as digits) to strings
representing the numbers.

It is assumed that this class will be instantiated only as part of
"svc_checker.py", and located in the same directory as the aforementioned
SVC model files, a TSV file of pairs of strings with differences, and the
"embed.dat" and "embed.vocab" files created from the Google News vectors.
"""

import os
import re

from fuzzywuzzy import fuzz
import numpy as np
from pyemd import emd
from sklearn.externals import joblib
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from scribie_num2text import num_to_text

class Checker():

    def __init__(self):

        model_file = "./SVC.pkl"
        self.model = joblib.load(model_file)
        self.eps = 1e-4

        w2v_dat = "./embed.dat"
        w2v_vocab = "./embed.vocab"

        # create word embeddings and mapping of vocabulary item to index
        self.embeddings = np.memmap(w2v_dat, dtype=np.float64,
                                    mode="r", shape=(3000000, 300))
        with open(w2v_vocab) as f:
            vocab_list = map(lambda string: string.strip(), f.readlines())
        self.vocab_dict = {w: i for i, w in enumerate(vocab_list)}

        # filler words that will be removed (regex)
        fillers = [
            "all right",
            "alright",
            "m+ hm+",
            "okay",
            "right",
            "uh huh",
            "yeah",
            "yes",
            "yup",

            "aha",
            "oh+",
            "hm+",
            "mm+",
            "um+",

            "actually",
            "basically",
            "i guess",
            "i mean",
            "i think",
            "kinda",
            "kind of",
            "like",
            "really",
            "sorta",
            "sort of",
            "you know",

            "a",
            "an",
            "the",

            "and",
            "in",
            "it",
            "of",
            "on",
            "or",
            "so",
            "that",
            "to",

            "am",
            #"is",

            "excuse me",
            "o'clock",
            "so to speak",
            "that's good",

            # speaker tracking
            "s\d+"
            ]

        metas = [
            "applause",
            "automated voice",
            "background conversation",
            "chuckle",
            "end paren",
            "foreign language",
            "laughter",
            "music",
            "noise",
            "overlapping conversation",
            "pause",
            "start paren",
            "video playback",
            "vocalization"
            ]

        # metas appear within [brackets], while fillers do not
        self._fillers = "\\b"+"\\b|\\b".join(fillers)+"\\b"
        self._metas = "\["+"\]|\[".join(metas)+"\]"


    def _indexer(self,string1,string2):
        """Get the summed index differences between the two strings, where the
        index is a proxy for the rarity of the word in the training corpus
        """

        # words in each string
        s1_features = string1.split()
        s2_features = string2.split()

        # indices of words in each string
        s1_idx = [self.vocab_dict[word] for word in s1_features]
        s2_idx = [self.vocab_dict[word] for word in s2_features]

        # sum index values of each string
        s1 = sum(s1_idx)
        s2 = sum(s2_idx)

        # normalized ratio
        diff = float(s2-s1)/(s2+s1+self.eps)

        return diff


    def _str_ratio(self,string1,string2):
        """Get the string similarity as a ratio from fuzzywuzzy, using
        Levenshtein (edit) distance
        """

        ratio = fuzz.ratio(string1,string2)

        return ratio


    def _wmd_getter(self,string1,string2):
        """Get the Word Mover's Distance between the two strings, using cosine
        distance between word2vec embeddings trained on Google News, and Earth
        Mover's Distance from pyemd
        """

        # count vectorize strings into tokens including a single <'>
        vect = CountVectorizer(token_pattern='[\w\']+').fit([string1,string2])
        features = vect.get_feature_names()

        # numpy memmap of vectors for each in-vocabulary word in the strings
        W_ = self.embeddings[[self.vocab_dict[word] for word in features]]

        # get 'flow' vectors; emd needs float64
        v_1,v_2 = vect.transform([string1,string2])
        v_1 = v_1.toarray().ravel().astype(np.float64)
        v_2 = v_2.toarray().ravel().astype(np.float64)

        # normalize vectors so as not to reward shorter strings in WMD calc
        v_1 /= (v_1.sum()+self.eps)
        v_2 /= (v_2.sum()+self.eps)

        # 1 minus cosine similarity is cosine distance
        D_cosine = 1.-cosine_similarity(W_).astype(np.float64)

        # using EMD (Earth Mover's Distance) from PyEMD
        wmd = emd(v_1,v_2,D_cosine)

        return wmd


    def _generate(self,str1,str2):
        """Return a numpy array of the values for each metric calclulated
        between the two strings
        """

        wmd = self._wmd_getter(str1,str2)
        idx = self._indexer(str1,str2)
        ratio = self._str_ratio(str1,str2)

        # proper format on which the model was trained
        values = np.asarray([wmd,idx,ratio]).reshape([1,-1])

        return values


    def _oov_clean(self,string):
        """Return the string with all OOVs removed
        """

        no_oov = " ".join([word for word in string.split()
                                if word in self.vocab_dict])

        return no_oov


    def _num_replace(self,string):
        """Replace digits with spelled-out versions of the numbers they
        represent, using scribie_num2text
        """

        # grab a list of digits and a list of their string representations
        nums = re.findall("\d+",string)
        # extra spaces are to preserve mixes of letters and numbers
        words = [" "+num_to_text(num)+" " for num in nums]

        # iteratively replace digits with strings
        for num,word in zip(nums,words):
            string = re.sub(num,word,string)

        return string

    def _cleaner(self,strings):
        """Return individual cleaned string with casing, punctuation, metas,
        and fillers removed, numbers converted to words, and OOVs converted
        or removed
        """

        cleaned = []

        for text in strings:

            # lowercase everything
            text = text.lower()

            # remove timestamps
            text = re.sub("\[*\d:\d+:\d+.\d\]*","",text)

            # remove dashes and colons
            text = re.sub("-|:"," ",text)

            # remove metas
            text = re.sub(self._metas,"",text)

            # keep only letters, numbers, spaces, and single <'>
            text = re.sub("[^a-z0-9 ']","",text)

            # certain casual forms
            text = re.sub("'til{1,2}","until",text)
            text = re.sub("'em","them",text)
            text = re.sub("'cause","because",text)
            text = re.sub("(d|g)oin'","\g<1>oing ",text)

            # british spellings that are OOV
            ### needs more thought. what about "treatise", "appraise", etc. ###
            #text = re.sub("(\w+i|y)s(ation|ing|e|es|ed|r)\\b","\\1z\\2",text)

            # contracted forms
            # lowercase "i'm" is OOV; currently removing "am" in fillers
            text = re.sub("'m"," am",text) #
            text = re.sub("'ve"," have",text)
            text = re.sub("'ll"," will",text)
            ### currently, cannot->can is major, vice-versa is minor ###
            # and can->can't and can't->can are minor
            text = re.sub("can't","cannot",text) #
            text = re.sub("won't","will not",text)
            text = re.sub("ain't","are not",text)
            text = re.sub("n't"," not",text)
            text = re.sub("'s\\b","",text)
            text = re.sub("'d\\b","",text)

            # ordinals
            text = re.sub("(\d*)(1st)","\g<1>0 first",text)
            text = re.sub("(\d*)(2nd)","\g<1>0 second",text)
            text = re.sub("(\d*)(3rd)","\g<1>0 third",text)
            text = re.sub("(\d+)(th)","\g<1>",text)

            # remove fillers
            text = re.sub(self._fillers,"",text)

            # replace digits
            text = self._num_replace(text)

            # remove OOVs and extra spaces
            clean = self._oov_clean(text)

            cleaned.append(clean)

        return cleaned


    def predict(self,tsv_file):
        """Print a string of predictions for each pair in the TSV file
        """

        # commented rows are for examining inputs to the model

        numpy_file = np.genfromtxt(tsv_file,
                                   dtype="str",delimiter="\t").reshape([-1,2])
        #numpy_file = np.genfromtxt(tsv_file,
        #                           dtype="str",delimiter="*").reshape([-1,2])

        predictions = []

        for row_ in numpy_file:

            # clean each string pair of all fillers, metas, numbers, OOVs, etc.
            row = self._cleaner(row_)

            s1,s2 = row[0],row[1]

            # complete deletions are considered minor
            if s2 == "":
                predictions.append("1")
                #predictions.extend([row_[0],row[0]])
                #predictions.extend([row_[1],row[1]])
                continue

            # empty first string with non-empty second is counted as major
            if s1 == "":
                predictions.append("2")
                #predictions.extend([row_[0],row[0]])
                #predictions.extend([row_[1],row[1]])
                continue

            # predict based on the trained SVC
            predictions.append(str(self.model.predict(self._generate(s1,s2))[0]))
            #predictions.extend([row_[0],row[0]])
            #predictions.extend([row_[1],row[1]])

        predictions = ",".join(predictions)
        #predictions = np.asarray(predictions).reshape([-1,5])

        print(predictions)
