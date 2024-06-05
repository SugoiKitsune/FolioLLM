import random
import re
import sys
import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer, AutoModelForCausalLM
import faiss
from faiss import write_index, read_index

etfs_num = 11796
vocab_size = 151936

model_name = '../pipeline/fine_tuned_model/FINGU-AI/FinguAI-Chat-v1'


def select_scores(
        heatmap, do_sample=False, top_p=0.2, top_k=50, temperature=1,
        sorted_return=True, standardize=False
):
    if standardize:
        heatmap -= heatmap.mean()
        heatmap /= heatmap.std()

    p = torch.nn.functional.softmax(heatmap, 0)
    val, ind = p.sort(descending=True)
    val, ind = val[:top_k], ind[:top_k]
    m = int((val.cumsum(0) < val.sum() * top_p).sum(dim=-1))

    if not do_sample: return ind[:m], torch.arange(m)

    T = 10 / (temperature + 1)
    ord_stat = torch.multinomial(val ** T, m, replacement=False)

    sorted_indices = ord_stat.sort()[0]
    return ind[sorted_indices], sorted_indices


# temperature increases randomness of sampling (0,+infty)
sel_cfg = {
    'do_sample': False,  #
    'temperature': 0,  #
    'top_p': 0.2,
    'top_k': 50,
}


class MultitaskLM(nn.Module):
    def __init__(self, body_path, class_path=None, select_path=None, sel_cfg=sel_cfg,
                 index_path="../../data/etfs.index", n_features=1024):
        super(MultitaskLM, self).__init__()
        self.model = AutoModelForCausalLM.from_pretrained(
            body_path, output_hidden_states=True
        )

        self.class_head = nn.Linear(n_features, 2, bias=False)
        self.select_head = nn.Linear(n_features, etfs_num, bias=False)

        # Add a projection layer to match the FAISS index dimensions
        self.projection = nn.Linear(n_features, 384, bias=False)

        if class_path:
            self.class_head.load_state_dict(torch.load(class_path))
        if select_path:
            self.select_head.load_state_dict(torch.load(select_path))

        self.out = None

        self.index = None
        self.init_index(index_path)
        self.model.eval()

    def forward(self, **kwargs):
        return self.model(**kwargs)

    def init_index(self, index_path):
        self.index = read_index(index_path)
        print(f"FAISS index dimensions: {self.index.d}")

    def classify(self, use_prev=False, **kwargs):
        if not use_prev:
            self.out = self.model(**kwargs).hidden_states[-1]
        embedding = self.encode(True)
        return self.class_head(embedding)

    def encode(self, use_prev=False, **kwargs):
        if not use_prev:
            self.out = self.model(**kwargs).hidden_states[-1]
        enc = self.out.mean(-2)
        normalized_enc = enc / torch.norm(enc)
        print(f"Embedding shape: {normalized_enc.shape}")
        return normalized_enc

    def select(self, use_prev=True, **kwargs):
        if not use_prev:
            self.out = self.model(**kwargs).hidden_states[-1]
        embedding = self.encode(True)
        projected_embedding = self.projection(embedding)
        print(f"Embedding shape before search: {projected_embedding.shape}")
        assert projected_embedding.shape[
                   -1] == self.index.d, "Embedding dimension does not match FAISS index dimension."
        distances, indices = self.index.search(projected_embedding.detach().numpy(), 50)
        return indices[0, :random.randint(3, 8)]


if __name__ == "__main__":
    m = MultitaskLM(model_name)
    text = "hello yann lecun"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokens = tokenizer(text, return_tensors='pt')

    # check if all works
    m.classify(**tokens)
    _ = m.select(**tokens)
