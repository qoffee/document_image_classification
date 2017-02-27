import os
import numpy as np
from collections import Counter, defaultdict
from string import punctuation, ascii_lowercase, digits
import operator
from itertools import chain, product
import logging
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from unidecode import unidecode

import subprocess
import multiprocessing as mp
from timeit import timeit
import time

import spacy

import seaborn as sns; sns.set()

from PIL import Image, ImageSequence

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity, pairwise_distances_argmin
from sklearn.decomposition import TruncatedSVD
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import Normalizer
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, silhouette_samples

from gensim import corpora, models, similarities
from gensim.parsing.preprocessing import STOPWORDS

from nltk.corpus import stopwords as sw


# run using %run -i <runfile.py> to avoid re-loading nlp as it takes time to complete
if not 'nlp' in locals():
    print 'Loading English Module...'
    nlp = spacy.load('en')
    print 'Completed Loading English Module.'


def remove_filename_spaces(directory):
    '''
    INPUT: main directory where data resides
    OUTPUT: None
    TASK: removes white space from file names since shell to open files in other functions don't recognize white space.
    '''
    for path, _, files in os.walk(directory):
        for f in files:
            if ' ' in f:
                os.rename(os.path.join(path, f), os.path.join(path, f.replace(' ', '')))


def doc_cnts_paths(data_path):
    '''
    INPUT: path to data repository
    OUTPUT: (8) specific file counts and paths based on file extensions (i.e. .tif, .xml, .txt)
    '''
    tif_cnt, xml_cnt, txt_cnt, misc_cnt = 0, 0, 0, 0
    tif_paths, xml_paths, txt_paths, misc_paths = [], [], [], []
    for dirpath, dirnames, filenames in os.walk(data_path):
        for file in filenames:
            if file.endswith('.tif'):
                tif_cnt += 1
                tif_paths.append(os.path.join(dirpath, file))
            elif file.endswith('.xml'):
                xml_cnt += 1
                xml_paths.append(os.path.join(dirpath, file))
            elif file.endswith('.txt'):
                txt_cnt += 1
                txt_paths.append(os.path.join(dirpath, file))
            else:
                misc_cnt += 1
                misc_paths.append(os.path.join(dirpath, file))
    return tif_cnt, xml_cnt, txt_cnt, misc_cnt, tif_paths, xml_paths, txt_paths, misc_paths


def img_info(tif_paths):
    '''
    INPUT: absolute paths to .tif documents
    OUTPUT: (1) prints counts of different compressions and dpi ranges for all .tif documents. Returns 'info' list containing absolute path to image, compression format, and dpi
    '''
    info = []
    comp_cnt, dpi_cnt = Counter(), Counter()
    for path in tif_paths:
        img = Image.open(path)
        info.append((path, img.info['compression'], img.info['dpi']))
    for desc in info:
        comp_cnt[desc[1]] += 1
        dpi_cnt[desc[2]] += 1
    print 'Compression Counts: {0} \nDPI Counts: {1}'.format(comp_cnt, dpi_cnt)
    return info


def shell_tesseract(path):
    '''
    INPUT: absolute path to .tif document
    TASK: performs OCR using tesseract from the shell. Creates a text file from the OCRd document using the same name and location as the .tif document.
    OUTPUT: None
    '''
    # tesseract automatically adds a .txt extension to the OCRd document. Name of new document is 3rd argument + .txt added by tesseract
    subprocess.call(['tesseract', path, path[:-4]])


def parallelize_OCR(tif_paths):
    '''
    INPUT: paths to .tif files.
    OUTPUT: time taken to complete task
    TASK: parallelize OCR of .tif files by calling shell_tesseract and using multiprocessing Pool.
    ISSUES: not tested as a function yet. Would like to print a progress report every 15 to 30 minutes.
    '''
    # parallelize OCR processing and time it
    pool = mp.Pool(processes=4)
    task = pool.map(shell_tesseract, tif_paths)
    return timeit(lambda: task, number=1)


def lemm_tokenize_doc(doc):
    '''
    INPUT: string that corresponds to a document in a raw corpus and a list of stop words.
    OUTPUT: (1) a list of tokens that corresponds to a corpus document. Strings are byte decoded, punctuation, digits, and newlines removed, words are lowered and lemmatized (words brought back to their 'base' form), only nouns are kept, non-words and stop-words are removed.
    PACKAGE USED: spaCy
    '''
    # decode bytes to utf-8 from doc
    ascii_doc = unidecode(doc.decode('utf-8'))

    # remove punctuation, digits, newlines, and lower the text
    clean_doc = ascii_doc.translate(None, punctuation).translate(None, digits).replace('\n', '').lower()

    # spaCy expects a unicode object
    spacy_doc = nlp(clean_doc.decode('utf-8'))

    # lemmatize, only keep nouns, transform to ascii as will no longer use spaCy
    noun_tokens = [unidecode(token.lemma_) for token in spacy_doc if token.pos_ == 'NOUN']

    # keep tokens longer than 2 characters
    long_tokens = [token for token in noun_tokens if len(token) >= 3]

    # remove tokens that have 3 equal consecutive characters
    triples = [''.join(triple) for triple in zip(ascii_lowercase, ascii_lowercase, ascii_lowercase)]
    good_tokens = [token for token in long_tokens if not [triple for triple in triples if triple in token]]

    # remove tokens that are present in stoplist
    stop_specific = ['wattenberg', 'yes', 'acre', 'number', 'mum', 'nwse', 'swne', 'lease', 'rule', 'drilling', 'permit', 'application', 'form', 'felfwl', 'fnlfsl', 'fnl', 'fsl', 'page', 'file', 'survey', 'facility', 'notice', 'sec', 'area', 'formation', 'corporation', 'phone', 'field', 'energy', 'company', 'production', 'fax', 'resource']

    NLTKstopwords = sw.words('english')

    stoplist = STOPWORDS.union(NLTKstopwords).union(stop_specific)

    final_tokens = [token for token in good_tokens if token not in stoplist]

    return final_tokens


def process_corpus(corpus_chunk):
    '''
    INPUT: equally sized chunks of raw corpus for pre-processing
    OUTPUT: (1) lemmatized and tokenized documents for the chunk of corpus supplied to the function.
    TASK: uses 'lemm_tokenize_doc' function to create a list of lemm-tokenized documents that correspond to all the documents in the chunk of raw corpus supplied.
    '''
    return [lemm_tokenize_doc(doc) for doc in corpus_chunk]


def parallel_corpus_lemm_tokenization(txt_paths):
    '''
    INPUT: paths to OCRd .tif files that are in .txt format.
    OUTPUT: (1) lemmatized and tokenized corpus
    TASK: use multiprocessing Pool to parallelize task using all cores on machine.
    '''
    raw_corpus = []
    for path in txt_paths:
        with open(path) as file:
            raw_corpus.append(file.read())

    cores = mp.cpu_count()
    n = len(txt_paths)/cores

    corpus_chunks = [raw_corpus[i:i + n] for i in xrange(0, len(raw_corpus), n)]

    pool = mp.Pool(processes=4)

    return list(chain(*pool.map(process_corpus, corpus_chunks)))


def bow_and_dict(tokenized_corpus, no_below, no_above=0.5):
    '''
    INPUT: lemmatized_corpus. 'no_below' helps with filtering out tokens that appear in less than the 'no_below' number of documents specified. 'no_above' is a fraction of the total corpus and it helps with filtering out tokens that appear in more than the 'no_above' fraction of documents specified. Basically, helps to filter out ubiquitous words that were not caught by stop_words.
    OUTPUT: (1) dictionary, which is a collection of all the unique tokens in the corpus. (2) Bag of words corpus, which represents each document in the corpus as a list of tuples with two elements - token id (referenced to the dictionary) and token frequency.
    TASK: tokenizes documents, creates dictionary from tokens, reduces size of dictionary based on 'no_below' and 'no_above' parameters.
    PACKAGE USED: gensim
    '''
    dictionary = corpora.Dictionary(tokenized_corpus)

    # words appearing in less than 'no_below' documents to be excluded from dictionary
    dictionary.filter_extremes(no_below=no_below)
    bow_corpus = [dictionary.doc2bow(text) for text in tokenized_corpus]

    return dictionary, bow_corpus
