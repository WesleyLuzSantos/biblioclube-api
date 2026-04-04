"""
BiblioClube API — v2 com PostgreSQL
Migrado de livros.json para banco de dados persistente.
"""

import os, json, uuid, hashlib, io, re
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
CORS(app, 
    origins=[
        'https://biblioclube.com.br',
        'https://www.biblioclube.com.br',
        'https://biblioclube.pages.dev',
        'https://escolas.biblioclube.com.br',
        'https://web-production-cb7fd.up.railway.app',
        'http://localhost:3000',
        'http://localhost:5000',
        'http://127.0.0.1:5500',
    ],
    supports_credentials=True,
    allow_headers=['Content-Type', 'X-Admin-Token', 'Authorization'],
    methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']
)

# ── CONFIG ─────────────────────────────────────────────────────────
ADMIN_SENHA  = os.environ.get('ADMIN_SENHA', 'biblioclube2026')
DATABASE_URL = os.environ.get('DATABASE_URL', '')
R2_ACCOUNT   = os.environ.get('R2_ACCOUNT_ID', '')
R2_KEY       = os.environ.get('R2_ACCESS_KEY', '')
R2_SECRET    = os.environ.get('R2_SECRET_KEY', '')
R2_BUCKET    = os.environ.get('R2_BUCKET', 'biblioclube-livros')
R2_PUBLIC    = os.environ.get('R2_PUBLIC_URL', '')

# ── BANCO ──────────────────────────────────────────────────────────
def get_db():
    url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url, cursor_factory=RealDictCursor)

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS livros (
                    id            TEXT PRIMARY KEY,
                    slug          TEXT UNIQUE,
                    titulo        TEXT NOT NULL,
                    autor         TEXT NOT NULL,
                    ano           TEXT DEFAULT '',
                    editora       TEXT DEFAULT '',
                    categoria     TEXT DEFAULT 'classico',
                    sinopse       TEXT DEFAULT '',
                    capa_url      TEXT DEFAULT '',
                    serie         TEXT DEFAULT '',
                    num_serie     INTEGER DEFAULT 0,
                    badge         TEXT DEFAULT '',
                    publicado     BOOLEAN DEFAULT TRUE,
                    destaque      BOOLEAN DEFAULT FALSE,
                    destaque_ate  DATE DEFAULT NULL,
                    pago          BOOLEAN DEFAULT FALSE,
                    link_afiliado TEXT DEFAULT '',
                    pdf_key       TEXT DEFAULT '',
                    paginas       INTEGER DEFAULT 0,
                    leituras      INTEGER DEFAULT 0,
                    criado_em     TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS paginas (
                    id         SERIAL PRIMARY KEY,
                    livro_id   TEXT REFERENCES livros(id) ON DELETE CASCADE,
                    num        INTEGER,
                    texto      TEXT,
                    UNIQUE(livro_id, num)
                );
                -- Adicionar colunas novas se já existir tabela antiga
                ALTER TABLE livros ADD COLUMN IF NOT EXISTS destaque_ate DATE DEFAULT NULL;
                ALTER TABLE livros ADD COLUMN IF NOT EXISTS pago BOOLEAN DEFAULT FALSE;
                ALTER TABLE livros ADD COLUMN IF NOT EXISTS link_afiliado TEXT DEFAULT '';
                ALTER TABLE livros ADD COLUMN IF NOT EXISTS leituras INTEGER DEFAULT 0;
            """)
            conn.commit()
    print("Banco inicializado")

try:
    init_db()
except Exception as e:
    print(f"Erro banco: {e}")

# ── HELPERS ────────────────────────────────────────────────────────
def get_r2():
    try:
        import boto3
        return boto3.client('s3',
            endpoint_url=f'https://{R2_ACCOUNT}.r2.cloudflarestorage.com',
            aws_access_key_id=R2_KEY,
            aws_secret_access_key=R2_SECRET,
            region_name='auto')
    except:
        return None

def verificar_admin():
    token = request.headers.get('X-Admin-Token', '')
    senha = ''
    if request.is_json:
        senha = (request.json or {}).get('senha', '')
    elif request.form:
        senha = request.form.get('senha', '')
    h = hashlib.sha256(ADMIN_SENHA.encode()).hexdigest()
    return token == h or hashlib.sha256(senha.encode()).hexdigest() == h

def fazer_slug(titulo):
    s = titulo.lower()
    for a, b in [('áàâã','a'),('éèê','e'),('íìî','i'),('óòôõ','o'),('úùû','u'),('ç','c')]:
        for c in a: s = s.replace(c, b)
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'\s+', '-', s.strip())
    return s[:50]

# ── API PÚBLICA ────────────────────────────────────────────────────

@app.route('/api/livros', methods=['GET'])
def listar_livros():
    cat = request.args.get('cat', '')
    q   = request.args.get('q', '').lower()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                sql    = "SELECT * FROM livros WHERE publicado = TRUE"
                params = []
                if cat:
                    sql += " AND categoria = %s"; params.append(cat)
                if q:
                    sql += " AND (LOWER(titulo) LIKE %s OR LOWER(autor) LIKE %s)"
                    params += [f'%{q}%', f'%{q}%']
                sql += " ORDER BY criado_em DESC"
                cur.execute(sql, params)
                rows = cur.fetchall()
        from datetime import date as _date
        hoje = _date.today()
        livros = [{
            'id': r['id'], 'slug': r['slug'] or r['id'],
            'titulo': r['titulo'], 'autor': r['autor'],
            'ano': r['ano'], 'editora': r['editora'],
            'categoria': r['categoria'], 'sinopse': r['sinopse'],
            'capa_url': r['capa_url'], 'paginas': r['paginas'],
            'tem_pdf': bool(r['pdf_key']),
            # Destaque válido só se não tiver data ou data ainda não venceu
            'destaque': bool(r['destaque']) and (not r['destaque_ate'] or r['destaque_ate'] >= hoje),
            'destaque_ate': r['destaque_ate'].isoformat() if r['destaque_ate'] else None,
            'pago': bool(r['pago']),
            'link_afiliado': r['link_afiliado'] or '',
            'leituras': r['leituras'] or 0,
            'serie': r['serie'], 'num_serie': r['num_serie'], 'badge': r['badge'],
        } for r in rows]
        return jsonify({'ok': True, 'livros': livros, 'total': len(livros)})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/livros/<livro_id>', methods=['GET'])
def detalhe_livro(livro_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM livros WHERE (id = %s OR slug = %s) AND publicado = TRUE",
                    (livro_id, livro_id))
                r = cur.fetchone()
        if not r:
            return jsonify({'ok': False, 'erro': 'Livro não encontrado'}), 404
        from datetime import date as _date2
        hoje2 = _date2.today()
        return jsonify({'ok': True, 'livro': {
            'id': r['id'], 'slug': r['slug'] or r['id'],
            'titulo': r['titulo'], 'autor': r['autor'],
            'ano': r['ano'], 'editora': r['editora'],
            'categoria': r['categoria'], 'sinopse': r['sinopse'],
            'capa_url': r['capa_url'], 'paginas': r['paginas'],
            'tem_pdf': bool(r['pdf_key']),
            'destaque': bool(r['destaque']) and (not r['destaque_ate'] or r['destaque_ate'] >= hoje2),
            'pago': bool(r['pago']),
            'link_afiliado': r['link_afiliado'] or '',
            'leituras': r['leituras'] or 0,
            'serie': r['serie'], 'num_serie': r['num_serie'], 'badge': r['badge'],
        }})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/livros/<livro_id>/registrar_leitura', methods=['POST'])
def registrar_leitura(livro_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE livros SET leituras = COALESCE(leituras,0) + 1 WHERE id = %s OR slug = %s",
                    (livro_id, livro_id))
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/livros/<livro_id>/paginas', methods=['GET'])
def paginas_livro(livro_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM livros WHERE id = %s OR slug = %s", (livro_id, livro_id))
                livro = cur.fetchone()
                if not livro:
                    return jsonify({'ok': False, 'erro': 'Livro não encontrado'}), 404
                cur.execute(
                    "SELECT num, texto FROM paginas WHERE livro_id = %s ORDER BY num",
                    (livro['id'],))
                paginas = cur.fetchall()
        if not paginas:
            return jsonify({'ok': False, 'erro': 'Páginas não processadas ainda'}), 404
        return jsonify({'ok': True, 'paginas': [dict(p) for p in paginas], 'total': len(paginas)})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


# ── ADMIN ──────────────────────────────────────────────────────────

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    d = request.get_json() or {}
    senha = d.get('senha', '')
    if hashlib.sha256(senha.encode()).hexdigest() == hashlib.sha256(ADMIN_SENHA.encode()).hexdigest():
        return jsonify({'ok': True, 'token': hashlib.sha256(ADMIN_SENHA.encode()).hexdigest()})
    return jsonify({'ok': False, 'erro': 'Senha incorreta'}), 401


@app.route('/api/admin/livros', methods=['GET'])
def admin_listar():
    if not verificar_admin(): return jsonify({'ok': False}), 401
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM livros ORDER BY criado_em DESC")
                rows = cur.fetchall()
        livros = [dict(r) | {'tem_pdf': bool(r['pdf_key'])} for r in rows]
        return jsonify({'ok': True, 'livros': livros})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/admin/livros', methods=['POST'])
def admin_criar():
    if not verificar_admin(): return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    if not d.get('titulo') or not d.get('autor'):
        return jsonify({'ok': False, 'erro': 'Título e autor obrigatórios'}), 400
    livro_id = str(uuid.uuid4())[:8]
    slug = d.get('slug', '').strip() or fazer_slug(d['titulo'])
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO livros
                      (id,slug,titulo,autor,ano,editora,categoria,sinopse,
                       capa_url,serie,num_serie,badge,publicado,destaque,
                       destaque_ate,pago,link_afiliado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                """, (livro_id, slug, d['titulo'], d['autor'],
                      d.get('ano',''), d.get('editora',''), d.get('categoria','classico'),
                      d.get('sinopse',''), d.get('capa_url',''),
                      d.get('serie',''), int(d.get('num_serie',0) or 0),
                      d.get('badge',''), bool(d.get('publicado',True)), bool(d.get('destaque',False)),
                      d.get('destaque_ate') or None,
                      bool(d.get('pago',False)), d.get('link_afiliado','')))
                livro = dict(cur.fetchone())
                conn.commit()
        livro['tem_pdf'] = False
        return jsonify({'ok': True, 'livro': livro})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/admin/livros/<livro_id>', methods=['PUT'])
def admin_editar(livro_id):
    if not verificar_admin(): return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    campos = ['titulo','autor','ano','editora','categoria','sinopse','capa_url',
              'serie','num_serie','badge','publicado','destaque','destaque_ate',
              'pago','link_afiliado','slug']
    sets = [f"{c} = %s" for c in campos if c in d]
    vals = [d[c] for c in campos if c in d]
    if not sets and 'paginas_texto' not in d:
        return jsonify({'ok': False, 'erro': 'Nada para atualizar'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if sets:
                    cur.execute(f"UPDATE livros SET {', '.join(sets)} WHERE id = %s RETURNING *",
                                vals + [livro_id])
                    livro = cur.fetchone()
                    if not livro:
                        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
                # Salvar páginas se enviadas
                paginas_texto = d.get('paginas_texto')
                if paginas_texto is not None:
                    cur.execute("DELETE FROM paginas WHERE livro_id = %s", (livro_id,))
                    for p in paginas_texto:
                        cur.execute(
                            "INSERT INTO paginas (livro_id, num, texto) VALUES (%s,%s,%s)",
                            (livro_id, p['num'], p['texto']))
                    count = d.get('paginas', len(paginas_texto))
                    cur.execute("UPDATE livros SET paginas = %s WHERE id = %s", (count, livro_id))
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/admin/livros/<livro_id>', methods=['DELETE'])
def admin_excluir(livro_id):
    if not verificar_admin(): return jsonify({'ok': False}), 401
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pdf_key FROM livros WHERE id = %s", (livro_id,))
                livro = cur.fetchone()
                if not livro: return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
                if livro['pdf_key']:
                    r2 = get_r2()
                    if r2:
                        try: r2.delete_object(Bucket=R2_BUCKET, Key=livro['pdf_key'])
                        except: pass
                cur.execute("DELETE FROM livros WHERE id = %s", (livro_id,))
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/admin/livros/<livro_id>/upload_pdf', methods=['POST'])
def admin_upload_pdf(livro_id):
    if not verificar_admin(): return jsonify({'ok': False}), 401
    if 'pdf' not in request.files: return jsonify({'ok': False, 'erro': 'PDF não enviado'}), 400
    try:
        pdf_bytes = request.files['pdf'].read()
        pdf_key   = f'livros/{livro_id}/{livro_id}.pdf'
        r2 = get_r2()
        if r2:
            r2.put_object(Bucket=R2_BUCKET, Key=pdf_key, Body=pdf_bytes, ContentType='application/pdf')
        paginas = []
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype='pdf')
            for i, page in enumerate(doc):
                texto = page.get_text('text').strip()
                if texto: paginas.append({'num': i+1, 'texto': texto})
            doc.close()
        except: pass
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE livros SET pdf_key=%s, paginas=%s WHERE id=%s",
                            (pdf_key, len(paginas), livro_id))
                cur.execute("DELETE FROM paginas WHERE livro_id=%s", (livro_id,))
                for p in paginas:
                    cur.execute("INSERT INTO paginas (livro_id,num,texto) VALUES (%s,%s,%s)",
                                (livro_id, p['num'], p['texto']))
                conn.commit()
        return jsonify({'ok': True, 'paginas': len(paginas), 'pdf_key': pdf_key})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/admin/livros/<livro_id>/upload_capa', methods=['POST'])
def admin_upload_capa(livro_id):
    if not verificar_admin(): return jsonify({'ok': False}), 401
    if 'capa' not in request.files: return jsonify({'ok': False, 'erro': 'Imagem não enviada'}), 400
    try:
        arquivo  = request.files['capa']
        ext_orig = arquivo.filename.rsplit('.', 1)[-1].lower() if '.' in arquivo.filename else 'jpg'
        dados    = arquivo.read()
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(dados))
            if img.mode in ('RGBA', 'P'): img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=90)
            dados, ext, ct = buf.getvalue(), 'jpg', 'image/jpeg'
        except:
            ext, ct = ext_orig, f'image/{ext_orig}'
        key = f'capas/{livro_id}/capa.{ext}'
        r2 = get_r2()
        if not r2: return jsonify({'ok': False, 'erro': 'Storage não configurado'}), 500
        r2.put_object(Bucket=R2_BUCKET, Key=key, Body=dados, ContentType=ct)
        url = f'{R2_PUBLIC}/{key}' if R2_PUBLIC else ''
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE livros SET capa_url=%s WHERE id=%s", (url, livro_id))
                conn.commit()
        return jsonify({'ok': True, 'url': url})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500


@app.route('/api/admin/alterar_senha', methods=['POST'])
def admin_alterar_senha():
    global ADMIN_SENHA
    if not verificar_admin(): return jsonify({'ok': False, 'erro': 'Não autorizado'}), 401
    d = request.get_json() or {}
    nova = d.get('senha_nova', '')
    atual = d.get('senha_atual', '')
    if len(nova) < 6: return jsonify({'ok': False, 'erro': 'Nova senha muito curta'}), 400
    if hashlib.sha256(atual.encode()).hexdigest() != hashlib.sha256(ADMIN_SENHA.encode()).hexdigest():
        return jsonify({'ok': False, 'erro': 'Senha atual incorreta'}), 401
    ADMIN_SENHA = nova
    os.environ['ADMIN_SENHA'] = nova
    return jsonify({'ok': True, 'token': hashlib.sha256(nova.encode()).hexdigest()})


@app.route('/admin')
def admin_page():
    return send_file('admin.html')


@app.route('/')
def health():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as total FROM livros WHERE publicado=TRUE")
                total = cur.fetchone()['total']
    except:
        total = 0
    return jsonify({'ok': True, 'app': 'BiblioClube API', 'livros': total, 'versao': '2.0.0', 'db': 'postgresql'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
