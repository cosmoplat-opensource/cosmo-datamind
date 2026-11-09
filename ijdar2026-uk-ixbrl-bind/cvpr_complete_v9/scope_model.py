"""Learned, nonrectangular header ownership with equal-input joint controls.

No observation ID, source ID or answer label is a trainable lookup. All controls
receive the same nodes, global context, semantic roles and candidate paths.
This module implements a mechanism; it does not establish an experimental gain.
"""
import math
import torch
from torch import nn

METHODS=('factor_memory','generic_joint','shared_neighborhood','explicit_scope')


class ScopeModel(nn.Module):
    def __init__(self, method, input_dim, hidden=128, roles=5):
        super().__init__()
        if method not in METHODS:raise ValueError('Unknown control')
        self.method=method;self.hidden=hidden;self.roles=roles
        self.projection=nn.Linear(input_dim,hidden)
        layer=nn.TransformerEncoderLayer(hidden,4,2*hidden,dropout=0,batch_first=True,norm_first=True)
        self.context=nn.TransformerEncoder(layer,2,enable_nested_tensor=False)
        self.scope_query=nn.Linear(hidden,hidden,bias=False)
        self.scope_key=nn.Linear(hidden,hidden,bias=False)
        self.header_unary=nn.Linear(hidden,1)
        self.cell_unary=nn.Linear(hidden,1)
        # Common perception and ownership modules; record each actual readout
        # size instead of padding controls with unused trainable parameters.
        self.role_bias=nn.Embedding(roles,hidden)
        self.query_projection=nn.Linear(input_dim,hidden)
        self.unknown_scope=nn.Parameter(torch.zeros(roles))
        if method=='factor_memory':
            self.factor=nn.ModuleList(nn.Linear(hidden+1,1) for _ in range(roles+1))
            self.interactions=nn.ModuleList(nn.Bilinear(hidden,hidden,1) for _ in range(roles))
        else:
            readout_size=2*hidden+roles if method=='shared_neighborhood' else (roles+1)*hidden+roles
            self.joint=nn.Sequential(nn.Linear(readout_size,hidden),nn.GELU(),nn.Linear(hidden,1))
            if method=='shared_neighborhood':self.role_attention=nn.Linear(hidden,roles)
        nn.init.zeros_(self.scope_query.weight)
        for layer in (self.header_unary,self.cell_unary):
            nn.init.zeros_(layer.weight);nn.init.zeros_(layer.bias)
        if method=='factor_memory':
            for layer in self.factor:
                nn.init.zeros_(layer.weight);nn.init.zeros_(layer.bias)
            for layer in self.interactions:
                nn.init.zeros_(layer.weight);nn.init.zeros_(layer.bias)
        else:nn.init.zeros_(self.joint[-1].weight);nn.init.zeros_(self.joint[-1].bias)

    def encode(self,nodes):
        if nodes.ndim!=2 or nodes.shape[0]==0:raise ValueError('Nonempty page nodes required')
        return self.context(self.projection(nodes).unsqueeze(0)).squeeze(0)

    def scope_logits(self,encoded,header_indices,cell_indices,header_roles):
        """One ownership logit per header/cell edge, not one enclosing rectangle."""
        h=encoded[header_indices]+self.role_bias(header_roles)
        c=encoded[cell_indices]
        if len(h)==0:return encoded.new_empty((0,len(c)))
        return (self.scope_query(h)@self.scope_key(c).T/math.sqrt(self.hidden)
                +self.header_unary(h)+self.cell_unary(c).T)

    def forward(self,nodes,header_indices,cell_indices,header_roles,query,paths,global_scope=True):
        """paths[P, roles+1]: first cell offset, then header offsets or -1.

        The scores are over common candidate paths. Coverage is shared across
        questions; candidate scores never reassign source header identities.
        Missing evidence remains a zero/unknown component, not invented support.
        """
        return self.score_encoded(self.encode(nodes),header_indices,cell_indices,header_roles,query,paths,global_scope)

    def score_encoded(self,enc,header_indices,cell_indices,header_roles,query,paths,global_scope=True,scope_logits=None):
        if paths.ndim!=2 or paths.shape[1]!=self.roles+1:raise ValueError('Invalid path arity')
        if (paths[:,0]<0).any() or (paths[:,0]>=len(cell_indices)).any():raise ValueError('Invalid cell index')
        if (paths[:,1:]>=len(header_indices)).any() or (paths[:,1:] < -1).any():raise ValueError('Invalid header index')
        scope=self.scope_logits(enc,header_indices,cell_indices,header_roles) if scope_logits is None else scope_logits
        if scope.shape!=(len(header_indices),len(cell_indices)):raise ValueError('Shared ownership shape mismatch')
        q=self.query_projection(query);cell=enc[cell_indices[paths[:,0]]]
        pieces=[cell*q];scope_terms=[]
        for role in range(self.roles):
            indices=paths[:,role+1];known=indices>=0
            values=torch.zeros_like(cell)
            term=torch.nn.functional.logsigmoid(self.unknown_scope[role]).expand(len(paths)).clone()
            if known.any():
                values[known]=enc[header_indices[indices[known]]]
                if global_scope:
                    term[known]=torch.nn.functional.logsigmoid(scope[indices[known],paths[known,0]])
            pieces.append(values*q);scope_terms.append(term)
        terms=torch.stack(scope_terms,-1)
        if not global_scope:terms=torch.zeros_like(terms)
        # All controls can compose role factors and global context. The distinct
        # bias is whether ownership is normalized per program or per header/cell.
        if self.method=='factor_memory':
            scalar=[cell.new_zeros((len(paths),1))]+[t[:,None] for t in scope_terms]
            if not global_scope:scalar=[torch.zeros_like(t) for t in scalar]
            factors=sum(layer(torch.cat((value,t),-1)).squeeze(-1)
                        for layer,value,t in zip(self.factor,pieces,scalar))
            interactions=sum(layer(pieces[0],pieces[r+1]).squeeze(-1) for r,layer in enumerate(self.interactions))
            scores=factors+interactions
        elif self.method=='generic_joint':scores=self.joint(torch.cat(pieces+[terms],-1)).squeeze(-1)
        elif self.method=='shared_neighborhood':
            attention=self.role_attention(cell).softmax(-1)
            pooled=sum(attention[:,r,None]*pieces[r+1] for r in range(self.roles))
            scores=self.joint(torch.cat((pieces[0],pooled,terms),-1)).squeeze(-1)
        else:
            neutral=self.roles*math.log(2) if global_scope else 0
            scores=self.joint(torch.cat(pieces+[terms],-1)).squeeze(-1)+terms.sum(-1)+neutral
        return {'scores':scores,'scope_logits':scope,'encoded_nodes':enc}


def latent_answer_loss(scores,eligible,compatible):
    """Marginalize all answer-compatible paths. Missing coverage gives no label."""
    if scores.shape!=eligible.shape or scores.shape!=compatible.shape:raise ValueError('Mismatched masks')
    if (compatible & ~eligible).any():raise ValueError('Ineligible path cannot be a positive')
    if not compatible.any():return scores.sum()*0
    return torch.logsumexp(scores[eligible],0)-torch.logsumexp(scores[compatible],0)


def partial_scope_loss(logits,target):
    """Only independently supported ownership edges supervise; NaN means unknown."""
    if logits.shape!=target.shape:raise ValueError('Ownership shape mismatch')
    known=torch.isfinite(target)
    if ((target[known]!=0)&(target[known]!=1)).any():raise ValueError('Ownership label must be binary or unknown')
    if not known.any():return logits.sum()*0
    return nn.functional.binary_cross_entropy_with_logits(logits[known],target[known])


def retained_scope_sets(logits,threshold):
    """Return discrete, possibly disconnected cell sets; threshold is calibrated elsewhere."""
    if not math.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('Invalid calibrated threshold')
    return [torch.nonzero(row.sigmoid()>=threshold,as_tuple=True)[0].tolist() for row in logits]
