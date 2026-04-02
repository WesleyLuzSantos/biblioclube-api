"""
BiblioClube API
Backend Flask para o site biblioclube.com.br
- Admin: cadastro de livros, upload de PDFs para R2
- API pública: lista de livros, detalhes, páginas para o leitor
- API para app escolar: acervo online
"""

import os, json, uuid, hashlib, io
from datetime import datetime
from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=[
    'https://biblioclube.com.br',
    'https://www.biblioclube.com.br',
    'https://biblioclube.pages.dev',
    'https://web-production-cb7fd.up.railway.app',  # app escolar
    'http://localhost:3000',
    'http://localhost:5000',
    'http://127.0.0.1:5500',
])

# ── CONFIG ─────────────────────────────────────────────────────────
ADMIN_SENHA = os.environ.get('ADMIN_SENHA', 'biblioclube2026')
R2_ACCOUNT  = os.environ.get('R2_ACCOUNT_ID', '')
R2_KEY      = os.environ.get('R2_ACCESS_KEY', '')
R2_SECRET   = os.environ.get('R2_SECRET_KEY', '')
R2_BUCKET   = os.environ.get('R2_BUCKET', 'biblioclube-livros')
R2_PUBLIC   = os.environ.get('R2_PUBLIC_URL', '')  # URL pública do bucket

DATA_FILE   = 'livros.json'  # persistência simples em JSON

# ── HELPERS ────────────────────────────────────────────────────────
def ler_livros():
    if os.path.exists(DATA_FILE):
        try:
            return json.load(open(DATA_FILE, 'r', encoding='utf-8'))
        except Exception:
            return []
    return []

def salvar_livros(livros):
    json.dump(livros, open(DATA_FILE, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

def get_r2():
    try:
        import boto3
        return boto3.client(
            's3',
            endpoint_url=f'https://{R2_ACCOUNT}.r2.cloudflarestorage.com',
            aws_access_key_id=R2_KEY,
            aws_secret_access_key=R2_SECRET,
            region_name='auto',
        )
    except Exception:
        return None

def verificar_admin():
    token = request.headers.get('X-Admin-Token', '')
    senha = request.json.get('senha', '') if request.is_json else request.form.get('senha', '')
    hash_ok = hashlib.sha256(ADMIN_SENHA.encode()).hexdigest()
    return token == hash_ok or hashlib.sha256(senha.encode()).hexdigest() == hash_ok

# ── API PÚBLICA ────────────────────────────────────────────────────

@app.route('/api/livros', methods=['GET'])
def listar_livros():
    """Lista todos os livros publicados — usado pelo site e pelo app escolar."""
    livros = ler_livros()
    cat    = request.args.get('cat', '')
    q      = request.args.get('q', '').lower()

    if cat:
        livros = [l for l in livros if l.get('categoria') == cat]
    if q:
        livros = [l for l in livros if
                  q in l.get('titulo','').lower() or
                  q in l.get('autor','').lower()]

    # Retorna campos públicos apenas
    publicos = [{
        'id':        l['id'],
        'titulo':    l['titulo'],
        'autor':     l['autor'],
        'ano':       l.get('ano', ''),
        'editora':   l.get('editora', ''),
        'categoria': l.get('categoria', ''),
        'sinopse':   l.get('sinopse', ''),
        'capa_url':  l.get('capa_url', ''),
        'paginas':   l.get('paginas', 0),
        'tem_pdf':   bool(l.get('pdf_key')),
        'slug':      l.get('slug', l['id']),
        'destaque':  l.get('destaque', False),
        'serie':     l.get('serie', ''),
        'num_serie': l.get('num_serie', 0),
        'badge':     l.get('badge', ''),
    } for l in livros if l.get('publicado', True)]

    return jsonify({'ok': True, 'livros': publicos, 'total': len(publicos)})


@app.route('/api/livros/<livro_id>', methods=['GET'])
def detalhe_livro(livro_id):
    """Detalhes de um livro específico."""
    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id or l.get('slug') == livro_id), None)
    if not livro:
        return jsonify({'ok': False, 'erro': 'Livro não encontrado'}), 404
    return jsonify({'ok': True, 'livro': {
        'id':        livro['id'],
        'titulo':    livro['titulo'],
        'autor':     livro['autor'],
        'ano':       livro.get('ano', ''),
        'editora':   livro.get('editora', ''),
        'categoria': livro.get('categoria', ''),
        'sinopse':   livro.get('sinopse', ''),
        'capa_url':  livro.get('capa_url', ''),
        'paginas':   livro.get('paginas', 0),
        'tem_pdf':   bool(livro.get('pdf_key')),
        'slug':      livro.get('slug', livro['id']),
        'serie':     livro.get('serie', ''),
        'num_serie': livro.get('num_serie', 0),
        'badge':     livro.get('badge', ''),
    }})


@app.route('/api/livros/<livro_id>/paginas', methods=['GET'])
def paginas_livro(livro_id):
    """Retorna as páginas extraídas do PDF para o leitor."""
    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id or l.get('slug') == livro_id), None)
    if not livro:
        return jsonify({'ok': False, 'erro': 'Livro não encontrado'}), 404

    paginas = livro.get('paginas_texto', [])
    if not paginas:
        return jsonify({'ok': False, 'erro': 'Páginas não processadas ainda'}), 404

    return jsonify({'ok': True, 'paginas': paginas, 'total': len(paginas)})


@app.route('/api/livros/<livro_id>/pdf', methods=['GET'])
def url_pdf(livro_id):
    """Retorna URL pré-assinada para download do PDF."""
    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id or l.get('slug') == livro_id), None)
    if not livro or not livro.get('pdf_key'):
        return jsonify({'ok': False, 'erro': 'PDF não disponível'}), 404
    r2 = get_r2()
    if not r2:
        return jsonify({'ok': False, 'erro': 'Storage não configurado'}), 500
    try:
        url = r2.generate_presigned_url(
            'get_object',
            Params={'Bucket': R2_BUCKET, 'Key': livro['pdf_key']},
            ExpiresIn=3600
        )
        return jsonify({'ok': True, 'url': url})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


# ── ADMIN ──────────────────────────────────────────────────────────

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """Verifica senha e retorna token de sessão."""
    d = request.get_json() or {}
    senha = d.get('senha', '')
    if hashlib.sha256(senha.encode()).hexdigest() == hashlib.sha256(ADMIN_SENHA.encode()).hexdigest():
        token = hashlib.sha256(ADMIN_SENHA.encode()).hexdigest()
        return jsonify({'ok': True, 'token': token})
    return jsonify({'ok': False, 'erro': 'Senha incorreta'}), 401


@app.route('/api/admin/livros', methods=['GET'])
def admin_listar():
    """Lista todos os livros (admin vê não publicados também)."""
    if not verificar_admin():
        return jsonify({'ok': False}), 401
    return jsonify({'ok': True, 'livros': ler_livros()})


@app.route('/api/admin/livros', methods=['POST'])
def admin_criar():
    """Cria novo livro (sem PDF — upload separado)."""
    if not verificar_admin():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    livro = {
        'id':         str(uuid.uuid4())[:8],
        'slug':       d.get('slug', '').strip() or d.get('titulo','').lower().replace(' ','-')[:40],
        'titulo':     d.get('titulo','').strip(),
        'autor':      d.get('autor','').strip(),
        'ano':        d.get('ano','').strip(),
        'editora':    d.get('editora','').strip(),
        'categoria':  d.get('categoria','classico'),
        'sinopse':    d.get('sinopse','').strip(),
        'capa_url':   d.get('capa_url','').strip(),
        'serie':      d.get('serie','').strip(),
        'num_serie':  int(d.get('num_serie',0) or 0),
        'badge':      d.get('badge','').strip(),
        'publicado':  bool(d.get('publicado', True)),
        'destaque':   bool(d.get('destaque', False)),
        'pdf_key':    '',
        'paginas':    0,
        'paginas_texto': [],
        'criado_em':  datetime.now().isoformat(),
    }
    if not livro['titulo']:
        return jsonify({'ok': False, 'erro': 'Título obrigatório'}), 400
    livros = ler_livros()
    livros.append(livro)
    salvar_livros(livros)
    return jsonify({'ok': True, 'livro': livro})


@app.route('/api/admin/livros/<livro_id>', methods=['PUT'])
def admin_editar(livro_id):
    """Edita dados de um livro."""
    if not verificar_admin():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id), None)
    if not livro:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    campos = ['titulo','autor','ano','editora','categoria','sinopse','capa_url',
              'serie','num_serie','badge','publicado','destaque','slug']
    for c in campos:
        if c in d:
            livro[c] = d[c]
    salvar_livros(livros)
    return jsonify({'ok': True, 'livro': livro})


@app.route('/api/admin/livros/<livro_id>', methods=['DELETE'])
def admin_excluir(livro_id):
    """Exclui um livro e seu PDF do R2."""
    if not verificar_admin():
        return jsonify({'ok': False}), 401
    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id), None)
    if not livro:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    # Remover PDF do R2 se existir
    if livro.get('pdf_key'):
        r2 = get_r2()
        if r2:
            try: r2.delete_object(Bucket=R2_BUCKET, Key=livro['pdf_key'])
            except Exception: pass
    livros = [l for l in livros if l['id'] != livro_id]
    salvar_livros(livros)
    return jsonify({'ok': True})


@app.route('/api/admin/livros/<livro_id>/upload_pdf', methods=['POST'])
def admin_upload_pdf(livro_id):
    """Faz upload do PDF para o R2 e extrai o texto das páginas."""
    if not verificar_admin():
        return jsonify({'ok': False}), 401
    if 'pdf' not in request.files:
        return jsonify({'ok': False, 'erro': 'Arquivo PDF não enviado'}), 400

    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id), None)
    if not livro:
        return jsonify({'ok': False, 'erro': 'Livro não encontrado'}), 404

    arquivo = request.files['pdf']
    pdf_bytes = arquivo.read()
    pdf_key   = f'livros/{livro_id}/{livro_id}.pdf'

    # Upload para R2
    r2 = get_r2()
    if r2:
        try:
            r2.put_object(
                Bucket=R2_BUCKET, Key=pdf_key,
                Body=pdf_bytes, ContentType='application/pdf'
            )
            livro['pdf_key'] = pdf_key
        except Exception as e:
            return jsonify({'ok': False, 'erro': f'Erro no upload: {e}'}), 500

    # Extrair texto do PDF com PyMuPDF
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        paginas = []
        for i, page in enumerate(doc):
            texto = page.get_text('text').strip()
            if texto:
                paginas.append({'num': i + 1, 'texto': texto})
        livro['paginas']       = len(doc)
        livro['paginas_texto'] = paginas
        doc.close()
    except ImportError:
        livro['paginas'] = 0
        livro['paginas_texto'] = []

    salvar_livros(livros)
    return jsonify({
        'ok': True,
        'paginas': livro['paginas'],
        'pdf_key': pdf_key,
    })


@app.route('/api/admin/livros/<livro_id>/upload_capa', methods=['POST'])
def admin_upload_capa(livro_id):
    """Faz upload da imagem de capa para o R2."""
    if not verificar_admin():
        return jsonify({'ok': False}), 401
    if 'capa' not in request.files:
        return jsonify({'ok': False, 'erro': 'Imagem não enviada'}), 400

    livros = ler_livros()
    livro  = next((l for l in livros if l['id'] == livro_id), None)
    if not livro:
        return jsonify({'ok': False, 'erro': 'Livro não encontrado'}), 404

    arquivo = request.files['capa']
    ext     = arquivo.filename.rsplit('.', 1)[-1].lower() if '.' in arquivo.filename else 'jpg'
    key     = f'capas/{livro_id}/capa.{ext}'
    dados   = arquivo.read()

    r2 = get_r2()
    if r2:
        try:
            r2.put_object(Bucket=R2_BUCKET, Key=key, Body=dados,
                          ContentType=f'image/{ext}', ACL='public-read')
            url = f'{R2_PUBLIC}/{key}' if R2_PUBLIC else ''
            livro['capa_url'] = url
            salvar_livros(livros)
            return jsonify({'ok': True, 'url': url})
        except Exception as e:
            return jsonify({'ok': False, 'erro': str(e)}), 500

    return jsonify({'ok': False, 'erro': 'Storage não configurado'}), 500


# ── ADMIN HTML ─────────────────────────────────────────────────────
@app.route('/admin')
def admin_page():
    from flask import send_file as sf
    return sf('admin.html')

# ── HEALTH ─────────────────────────────────────────────────────────
@app.route('/')
def health():
    livros = ler_livros()
    return jsonify({
        'ok':      True,
        'app':     'BiblioClube API',
        'livros':  len([l for l in livros if l.get('publicado', True)]),
        'versao':  '1.0.0',
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
