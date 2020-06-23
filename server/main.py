from typing import *
from pathlib import Path
import argparse
import numpy as np

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
import uvicorn
from pydantic import BaseModel

import utils.path_fixes as pf
import api

from utils.f import ifnone
import config

from data_processing import from_model
from transformer_details import get_details

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--debug", action="store_true", help=" Debug mode")
parser.add_argument("--port", default=5050, help="Port to run the app. ")
parser.add_argument("--model", default=None, help="If given, override the backend to use a particular model from local storage. Corpus searching results will only be availbale if annotated. Also requires specifying 'kind'")
parser.add_argument("--kind", default=None, help="One of {'bidirectional', 'autoregressive'}. Required if model provided.")
args, _ = parser.parse_known_args()

class ArgConfig():
    def __init__(self, args):
        self.args = args
        self.model = args.model
        self.kind = args.kind

        if not (self.has_model or self.use_defaults):
            raise ValueError("Either specify both `model` and `kind` or neither to use the defaults")

    @property
    def has_model(self):
        return self.model is not None and self.kind is not None
    
    @property
    def model_name(self):
        if self.has_model:
            return Path(self.model).stem

        return None

    @property
    def use_defaults(self):
        return self.model is None and self.kind is None

    def from_pretrained(self, model_name:str):
        if self.has_model:
            return get_details(self.model)

        return get_details(model_name)

aconf = ArgConfig(args)

# Default routing
@app.get("/")
def hello_world():
    return RedirectResponse(url="client/exBERT.html")

# send everything from client as static content
@app.get("/client/{file_path:path}")
def send_static_client(file_path):
    """ serves all files from ./client/ to ``/client/<path:path>``

    :param path: path from api call
    """
    f = str(pf.CLIENT_DIST / file_path)
    return FileResponse(f)

# ======================================================================
## MAIN API ##
# ======================================================================
@app.get("/api/supported-models")
async def get_supported_models():
    if aconf.has_model:
        return [
            {
                "name": aconf.model_name,
                "kind": aconf.kind
            }
        ]

    return config.SUPPORTED_MODELS

@app.get("/api/get-model-details")
async def get_model_details(model: str, request_hash=None):# -> api.ModelDetailResponse:
    deets = aconf.from_pretrained(model)

    info = deets.model.config
    nlayers = info.num_hidden_layers
    nheads = info.num_attention_heads

    payload_out = {
        "nlayers": nlayers,
        "nheads": nheads,
    }

    return {
        "status": 200,
        "payload": payload_out,
    }

@app.get("/api/attend-with-meta")
async def get_attentions_and_preds(
    model: str, sentence: str, layer: int, request_hash=None
):# -> api.AttentionResponse:
    details = aconf.from_pretrained(model)

    deets = details.att_from_sentence(sentence)

    payload_out = deets.to_json(layer)

    return {
        "status": 200,
        "payload": payload_out
    }


@app.post("/api/update-mask")
async def update_masked_attention(
    payload: api.MaskUpdatePayload,
):# -> api.AttentionResponse:
    """
    Return attention information from tokens and mask indices.

    Object: {"a" : {"sentence":__, "mask_inds"}, "b" : {...}}
    """
    model = payload.model
    details = aconf.from_pretrained(model)

    tokens = payload.tokens
    sentence = payload.sentence
    mask = payload.mask
    layer = payload.layer

    MASK = details.aligner.mask_token
    mask_tokens = lambda toks, maskinds: [
        t if i not in maskinds else ifnone(MASK, t) for (i, t) in enumerate(toks)
    ]

    token_inputs = mask_tokens(tokens, mask)

    deets = details.att_from_tokens(token_inputs, sentence)
    payload_out = deets.to_json(layer)

    return {
        "status": 200,
        "payload": payload_out,
    }


# def nearest_embedding_search(**request):
@app.post("/api/k-nearest-embeddings")
async def nearest_embedding_search(payload:api.QueryNearestPayload):
    """Return the token text and the metadata in JSON"""
    model = payload.model
    corpus = payload.corpus
    embedding = payload.embedding
    layer = payload.layer
    heads = payload.heads
    k = payload.k

    try:
        details = aconf.from_pretrained(model)
    except KeyError as e:
        return {'status': 405, "payload": None}

    model_name = ifnone(aconf.model_name, model)

    try:
        cc = from_model(model_name, corpus)
    except FileNotFoundError as e:
        return {
            "status": 406,
            "payload": None
        }

    q = np.array(embedding).reshape((1, -1)).astype(np.float32)
    heads = list(set(heads))

    out = cc.search_embeddings(layer, q, k)

    payload_out = [o.to_json(layer, heads) for o in out]

    return {
        "status": 200,
        "payload": payload_out
    }


# def nearest_context_search(**request):
@app.post("/api/k-nearest-contexts")
async def nearest_context_search(payload:api.QueryNearestPayload):
    """Return the token text and the metadata in JSON"""
    model = payload.model
    corpus = payload.corpus
    context = payload.embedding
    layer = payload.layer
    heads = payload.heads
    k = payload.k

    try:
        details = aconf.from_pretrained(model)
    except KeyError as e:
        return {'status': 405, "payload": None}

    model_name = ifnone(aconf.model_name, model)

    try:
        cc = from_model(model_name, corpus)
    except FileNotFoundError as e:
        return {'status': 406, "payload": None}

    q = np.array(context).reshape((1, -1)).astype(np.float32)
    heads = list(set(heads))

    out = cc.search_contexts(layer, heads, q, k)
    payload_out = [o.to_json(layer, heads) for o in out]

    return {
        "status": 200,
        "payload": payload_out,
    }

# Setup code
if __name__ == "__main__":
    print("Initializing as the main script")  # Is never printed
    args, _ = parser.parse_known_args()
    uvicorn.run("main:app", host="127.0.0.1", port=args.port)