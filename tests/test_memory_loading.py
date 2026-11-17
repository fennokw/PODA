"""Opening Memory must not run inference, reflow saved nodes or load Open3D."""
import builtins
import math
from poda_app.memory import layout


def test_scene_uses_saved_geometry_without_inference(client,monkeypatch):
    def forbidden(*a,**k): raise AssertionError('Opening a page must not reflow memory')
    monkeypatch.setattr(layout,'ensure_positions',forbidden)
    monkeypatch.setattr(layout,'semantic_layout',forbidden)
    original=builtins.__import__
    def no_open3d(name,*a,**k):
        if name.startswith('open3d'): raise AssertionError('Native geometry import on scene read')
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',no_open3d)
    one=client.get('/memory/open3d/scene')
    assert one.status_code==200
    two=client.get('/memory/open3d/scene').json()
    assert two['semantic_layout']['initialized']==0
    assert [(n['id'],n['x'],n['y'],n['z']) for n in one.json()['nodes']]==[(n['id'],n['x'],n['y'],n['z']) for n in two['nodes']]


def test_portable_geometry_and_bounds():
    mesh=layout.open3d_geometry_template()['cloud_volume']
    assert mesh['triangle_count']>1000
    assert all(abs(sum(v*v for v in p)-1)<1e-5 for p in mesh['vertices'])
    assert all(0<=i<len(mesh['vertices']) for tri in mesh['triangles'] for i in tri)
    bounds=layout.scene_bounds([{'x':-1,'y':2,'z':0},{'x':3,'y':6,'z':0}])
    assert bounds['center']==[1,4,0] and bounds['extent']==[4,4,0]
    assert bounds['diagonal']==math.sqrt(32)
