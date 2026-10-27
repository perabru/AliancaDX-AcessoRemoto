#!/usr/bin/env python3
"""Build the illustrated AliançaDX user manual from reviewed JSON content.

Usage: python docs/manual/build_manual.py --output /absolute/Manual_AliancaDX.pdf
Requires reportlab and the DejaVu Sans fonts; all illustrations are vectors.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BRAND = ROOT / 'assets' / 'brand'
W, H = 595.2756, 841.8898
M, BOTTOM = 44, 65
CW = W - 2*M
C = {k: colors.HexColor(v) for k,v in {
    'forest':'#123A2A','deep':'#073B2B','green':'#007A50','neon':'#02EF5F',
    'mint':'#E5F4EC','surface':'#F4FAF7','muted':'#446557','line':'#CEE6D9',
    'amber':'#8B5414','amberbg':'#FFF5E6','white':'#FFFFFF','grey':'#DCEAE3'
}.items()}

ORDER = ['cover','quick','contents','roles','downloads',
 'win_install','host_ready','invite','win_connect','consent','win_controls',
 'win_multi','win_files','saved','android_install','android_origin',
 'android_connect','android_controls','android_multi','android_files',
 'ios_install','ios_pc','ios_offer','ios_answer','ios_controls','ios_multi_files',
 'network','stop','usb','maintenance','connect_fix','install_fix','use_fix',
 'ios_fix','lan','identity','support','references']
PAGES = {key: i+1 for i,key in enumerate(ORDER)}

def clean(s):
    return str(s).replace('\u2011','-').replace('\u2013','-').replace('\u2014','-')

def fonts():
    base=Path('/usr/share/fonts/truetype/dejavu')
    for name, file in [('Body','DejaVuSans.ttf'),('Bold','DejaVuSans-Bold.ttf'),('Mono','DejaVuSansMono.ttf')]:
        pdfmetrics.registerFont(TTFont(name,str(base/file)))
    pdfmetrics.registerFontFamily('Body',normal='Body',bold='Bold',italic='Body',boldItalic='Bold')

class Manual:
    def __init__(self, path, content):
        self.path=path; self.content=content; self.page=0; self.y=0; self.key=''
        self.c=canvas.Canvas(str(path),pagesize=(W,H),pageCompression=1)
        self.c.setTitle('AliançaDX Acesso Remoto - Manual do usuário')
        self.c.setAuthor('AliançaDX | Bruno Michel Pera')
        self.c.setCreator('AliançaDX - Manual do usuário 7.1.0')
        self.c.setSubject('Windows 7.1.0, Android 7.1.0 e iPhone/iPad PWA 7.1.0')
        self.c.setKeywords('AliançaDX, acesso remoto, manual, Windows, Android, iPhone, iPad, PWA')
        self.c.setViewerPreference('DisplayDocTitle','true')
        self.c.showOutline()
        self.metrics=[]

    def style(self,size=11,leading=None,color='forest',bold=False):
        return ParagraphStyle('text',fontName='Bold' if bold else 'Body',fontSize=size,
            leading=leading or size*1.46,textColor=C[color],alignment=TA_LEFT,
            spaceAfter=0,allowWidows=0,allowOrphans=0,splitLongWords=True)

    def para_size(self,text,width,size=11,leading=None,color='forest',bold=False):
        text=re.sub(r'\s+(\[A[0-9, A]+\])',r'&nbsp;\1',clean(text))
        p=Paragraph(text,self.style(size,leading,color,bold))
        return p,p.wrap(width,H)[1]

    def at(self,text,x,y,width,size=11,leading=None,color='forest',bold=False):
        p,h=self.para_size(text,width,size,leading,color,bold)
        p.drawOn(self.c,x,y-h)
        return h

    def reserve(self,h):
        if self.y-h < BOTTOM:
            raise ValueError(f'Page {self.page} ({self.key}) overflows: y={self.y:.1f}, block={h:.1f}')

    def p(self,text,size=11.1,color='forest',gap=13):
        p,h=self.para_size(text,CW,size,color=color)
        self.reserve(h+gap);p.drawOn(self.c,M,self.y-h);self.y-=h+gap

    def label(self,text,x,y,size=8.4,color='green'):
        self.c.setFillColor(C[color]);self.c.setFont('Bold',size);self.c.drawString(x,y,clean(text))

    def pill(self,text,x,y,w=None,fill='mint',color='green',h=24,size=9):
        if w is None:w=pdfmetrics.stringWidth(clean(text),'Bold',size)+20
        self.c.setFillColor(C[fill]);self.c.roundRect(x,y,w,h,h/2,fill=1,stroke=0)
        self.c.setFillColor(C[color]);self.c.setFont('Bold',size);self.c.drawCentredString(x+w/2,y+(h-size)/2+1.5,clean(text))

    def start(self,key,section,title,subtitle):
        if self.page:self.finish()
        self.page+=1;self.key=key
        self.c.bookmarkPage(key)
        self.c.addOutlineEntry(clean(title),key,level=0,closed=False)
        self.c.setFillColor(C['white']);self.c.rect(0,0,W,H,fill=1,stroke=0)
        self.c.setFillColor(C['green']);self.c.rect(0,H-8,W,8,fill=1,stroke=0)
        self.c.drawImage(str(BRAND/'aliancadx-logo.png'),M,H-66,width=125,height=29.23,mask='auto')
        self.c.setFont('Body',8.1);self.c.setFillColor(C['muted'])
        self.c.drawRightString(W-M,H-46,'MANUAL DO USUÁRIO')
        self.c.drawRightString(W-M,H-59,'EDIÇÃO 27 OUT 2026')
        self.c.setStrokeColor(C['line']);self.c.setLineWidth(.7);self.c.line(M,H-80,W-M,H-80)
        self.label(section.upper(),M,H-108)
        title_h=self.at(title,M,H-124,CW,size=25.2,leading=31.3,bold=True)
        self.y=H-124-title_h-12
        h=self.at(subtitle,M,self.y,CW,size=10.9,leading=16,color='muted')
        self.y-=h+23

    def finish(self):
        if self.page>1:
            self.c.setStrokeColor(C['line']);self.c.setLineWidth(.6);self.c.line(M,49,W-M,49)
            self.c.setFont('Bold',8);self.c.setFillColor(C['green']);self.c.drawString(M,32,'SUMÁRIO')
            self.c.linkRect('', 'contents',(M,27,M+49,43),relative=0,thickness=0)
            self.c.setFont('Body',8);self.c.setFillColor(C['muted']);self.c.drawString(M+71,32,'ALIANÇADX  /  ACESSO REMOTO')
            self.c.setFont('Bold',9);self.c.drawRightString(W-M,32,f'{self.page:02d} / {len(ORDER):02d}')
        self.metrics.append({'page':self.page,'key':self.key,'bottom':round(self.y,2)})
        self.c.showPage()

    def steps(self,items):
        for i,(title,text) in enumerate(items,1):
            titlep,th=self.para_size(title,CW-59,11.6,16,bold=True)
            bp,bh=self.para_size(text,CW-59,10.5,15.2)
            h=max(53,th+bh+23)
            self.reserve(h+9);y=self.y-h
            self.c.setFillColor(C['surface']);self.c.roundRect(M,y,CW,h,11,fill=1,stroke=0)
            self.c.setFillColor(C['green']);self.c.circle(M+22,self.y-24,12,fill=1,stroke=0)
            self.c.setFillColor(C['white']);self.c.setFont('Bold',10.5);self.c.drawCentredString(M+22,self.y-27.5,str(i))
            titlep.drawOn(self.c,M+45,self.y-11-th)
            bp.drawOn(self.c,M+45,self.y-15-th-bh)
            self.y-=h+9
        self.y-=3

    def note(self,title,text,tone='info'):
        bg='amberbg' if tone=='warn' else 'mint'; fg='amber' if tone=='warn' else 'green'
        tp,th=self.para_size(title,CW-31,11.1,15,bold=True,color=fg)
        bp,bh=self.para_size(text,CW-31,10.4,15.1)
        h=th+bh+29;self.reserve(h+13)
        self.c.setFillColor(C[bg]);self.c.roundRect(M,self.y-h,CW,h,9,fill=1,stroke=0)
        self.c.setFillColor(C[fg]);self.c.roundRect(M,self.y-h,4,h,2,fill=1,stroke=0)
        tp.drawOn(self.c,M+16,self.y-12-th);bp.drawOn(self.c,M+16,self.y-17-th-bh)
        self.y-=h+13

    def bullets(self,items):
        for text in items:
            p,h=self.para_size(text,CW-20,10.9,15.8)
            self.reserve(h+10);self.c.setFillColor(C['green']);self.c.circle(M+4,self.y-7,2.3,fill=1,stroke=0)
            p.drawOn(self.c,M+17,self.y-h);self.y-=h+10
        self.y-=3

    def table(self,headers,rows,widths=None):
        n=len(headers);widths=widths or ([.34,.66] if n==2 else [1/n]*n)
        widths=[v/sum(widths)*CW for v in widths]
        all_rows=[headers]+rows
        pad=7 if len(rows)>=6 else 8
        for ri,row in enumerate(all_rows):
            if len(row)!=n:raise ValueError('Bad table width')
            cells=[self.para_size(cell,widths[i]-20,size=9.8 if ri else 9.4,leading=14.3 if ri else 13.5,color='forest' if ri else 'white',bold=not ri) for i,cell in enumerate(row)]
            h=max(x[1] for x in cells)+2*pad
            self.reserve(h+3)
            self.c.setFillColor(C['green'] if ri==0 else C['surface'] if ri%2 else C['white'])
            self.c.rect(M,self.y-h,CW,h,fill=1,stroke=0)
            x=M
            for i,(p,ch) in enumerate(cells):p.drawOn(self.c,x+10,self.y-pad-ch);x+=widths[i]
            self.c.setStrokeColor(C['line']);self.c.setLineWidth(.4);self.c.line(M,self.y-h,W-M,self.y-h)
            self.y-=h
        self.y-=17

    def stats(self,items):
        gap=11;w=(CW-gap*(len(items)-1))/len(items);h=94;self.reserve(h+17)
        for i,(number,label) in enumerate(items):
            x=M+i*(w+gap);self.c.setFillColor(C['surface']);self.c.roundRect(x,self.y-h,w,h,12,fill=1,stroke=0)
            self.label(number,x+14,self.y-39,size=27)
            self.at(label,x+14,self.y-49,w-28,size=9.6,leading=13.4,color='muted')
        self.y-=h+17

    def link(self,label,url):
        h=39;self.reserve(h+13)
        self.c.setFillColor(C['green']);self.c.roundRect(M,self.y-h,CW,h,9,fill=1,stroke=0)
        self.c.setFillColor(C['white']);self.c.setFont('Bold',10.3);self.c.drawString(M+14,self.y-24,clean(label))
        self.c.setFont('Bold',15);self.c.drawRightString(W-M-14,self.y-25,'→')
        if url.startswith('#'):self.c.linkRect('',url[1:],(M,self.y-h,W-M,self.y),relative=0,thickness=0)
        else:self.c.linkURL(url,(M,self.y-h,W-M,self.y),relative=0,thickness=0)
        self.y-=h+13

    def mock(self,kind):
        if kind=='pairing':return self.pairing()
        if kind in ('android','iphone'):return self.phone_mock(kind)
        h=159;self.reserve(h+28);top=self.y;bottom=top-h
        self.c.setFillColor(C['surface']);self.c.setStrokeColor(C['line']);self.c.roundRect(M,bottom,CW,h,11,fill=1,stroke=1)
        self.c.setFillColor(C['green']);self.c.roundRect(M,top-29,CW,29,10,fill=1,stroke=0)
        self.c.rect(M,top-29,CW,13,fill=1,stroke=0)
        self.label('ALIANÇADX  /  '+('HOSPEDEIRO' if kind=='host' else 'CONTROLADOR'),M+14,top-19,size=9,color='white')
        if kind=='host':
            self.label('Convite para este computador',M+17,top-55,size=10)
            self.pill('DISPONÍVEL',W-M-113,top-62,w=96,h=21,size=8)
            self.c.setFillColor(C['white']);self.c.roundRect(M+17,top-99,286,29,5,fill=1,stroke=0)
            self.label('Código privado  •••••••••••••••',M+27,top-88,size=9,color='muted')
            self.pill('Copiar convite',M+317,top-100,w=171,h=30,fill='green',color='white',size=9)
            self.pill('Parar acesso',M+17,bottom+15,w=139,h=25,size=9)
            self.pill('iPhone e iPad',M+168,bottom+15,w=139,h=25,size=9)
        else:
            self.c.setFillColor(C['deep']);self.c.rect(M+1,bottom+1,102,h-30,fill=1,stroke=0)
            for i,t in enumerate(['Conectar','Sessão remota','Computadores']):self.label(t,M+10,top-54-i*26,size=7.5,color='white')
            self.label('Código de conexão',M+119,top-52,size=9.7)
            self.c.setFillColor(C['white']);self.c.roundRect(M+119,top-91,CW-139,27,5,fill=1,stroke=0)
            self.label('Cole aqui o código privado',M+128,top-81,size=8.6,color='muted')
            self.pill('Validar código e continuar',M+119,bottom+16,w=CW-139,h=29,fill='green',color='white',size=9)
        self.at('Mapa ilustrado da interface. Siga os nomes dos campos e botões.',M,bottom-8,CW,size=8.1,leading=11,color='muted')
        self.y=bottom-30

    def phone_mock(self,kind):
        h=210;self.reserve(h+31);top=self.y;bottom=top-h;x=M+3;pw=154
        self.c.setFillColor(C['deep']);self.c.roundRect(x,bottom,pw,h,17,fill=1,stroke=0)
        self.c.setFillColor(C['white']);self.c.roundRect(x+5,bottom+5,pw-10,h-10,12,fill=1,stroke=0)
        self.label('ALIANÇADX',x+14,top-25,size=8)
        self.label('PC selecionado',x+14,top-43,size=7.4,color='muted')
        self.c.setFillColor(C['deep']);self.c.rect(x+10,bottom+70,pw-20,89,fill=1,stroke=0)
        self.c.setFillColor(C['green']);self.c.roundRect(x+22,bottom+111,68,30,4,fill=1,stroke=0)
        self.c.setStrokeColor(C['neon']);self.c.setLineWidth(1.6);self.c.circle(x+96,bottom+108,7,fill=0,stroke=1)
        self.label('MOUSEPAD',x+15,bottom+149,size=6.1,color='mint')
        controls=[(x+47,bottom+125,'Esquerdo',False,False),
                  (x+109,bottom+125,'Direito',True,False),
                  (x+47,bottom+87,'Duplo',False,True),
                  (x+109,bottom+87,'Arrastar',False,False)]
        for cx,cy,label,right,double in controls:
            self.c.saveState()
            self.c.setFillColor(colors.HexColor('#14272A'));self.c.setFillAlpha(.72)
            self.c.setStrokeColor(C['mint']);self.c.setStrokeAlpha(.55);self.c.setLineWidth(.7)
            self.c.circle(cx,cy,17,fill=1,stroke=1)
            self.c.restoreState()
            self.c.setStrokeColor(C['white']);self.c.setLineWidth(.8)
            self.c.roundRect(cx-5,cy-2,10,14,4,fill=0,stroke=1)
            self.c.line(cx,cy+5,cx,cy+10);self.c.line(cx-4,cy+5,cx+4,cy+5)
            self.c.setFillColor(C['neon']);self.c.roundRect(cx+1 if right else cx-4,cy+6,3,4,1,fill=1,stroke=0)
            if double:self.label('2x',cx+7,cy+8,size=5.5,color='mint')
            self.c.setFillColor(C['white']);self.c.setFont('Bold',5.7)
            self.c.drawCentredString(cx,cy-11,label)
        self.pill('Teclado' if kind=='iphone' else 'Controles',x+14,bottom+39,w=61,h=22,size=6.7)
        self.pill('Arquivos',x+79,bottom+39,w=61,h=22,size=6.7)
        right=M+180;rw=CW-180
        self.label('MOVIMENTO E CLIQUE SEPARADOS',right,top-12,size=8.2)
        y=top-29
        descriptions=[('01','Deslize para mover','Use toda a área remota. Reposicione o dedo sem deslocar o cursor.'),
                      ('02','Clique pelos botões','Esquerdo, Direito e Duplo atuam no cursor. O toque na tela não clica.'),
                      ('03','Arraste com indicação','Ative Arrastar, mova e toque em Soltar. O estado ativo fica destacado.')]
        for number,title,text in descriptions:
            self.label(number,right,y-13,size=15)
            th=self.at(title,right+34,y,rw-34,size=10.5,leading=14.5,bold=True)
            bh=self.at(text,right+34,y-th-4,rw-34,size=10.1,leading=14.6,color='muted')
            y-=th+bh+19
        self.at('Ilustração da interface; não é uma captura de aparelho físico.',M,bottom-9,CW,size=8.1,leading=11,color='muted')
        self.y=bottom-31

    def pairing(self):
        h=185;self.reserve(h+24);top=self.y;bottom=top-h
        left=M+9;right=W-M-150
        for x,label in [(left,'PC / CONECTOR'),(right,'IPHONE / PWA')]:
            self.c.setFillColor(C['surface']);self.c.roundRect(x,bottom+36,141,h-36,11,fill=1,stroke=0)
            self.label(label,x+11,top-21,size=8.5)
        middle=M+159;mw=CW-318
        for i,(t,forward) in enumerate([('1. CONVITE',True),('2. RESPOSTA',False)]):
            y=top-60-i*59;self.c.setStrokeColor(C['green']);self.c.setLineWidth(1.7)
            self.c.line(left+118,y,right+23,y)
            target=right+23 if forward else left+118;sgn=-1 if forward else 1
            self.c.line(target,y,target+sgn*7,y+4);self.c.line(target,y,target+sgn*7,y-4)
            self.c.setFillColor(C['white']);self.c.roundRect(middle-8,y+7,mw+16,20,4,fill=1,stroke=0)
            self.c.setFillColor(C['green']);self.c.setFont('Bold',8.5);self.c.drawCentredString(W/2,y+14,t)
        self.pill('3. AUTORIZAÇÃO NO HOSPEDEIRO',M,bottom,w=CW,h=27,fill='green',color='white',size=9)
        self.y=bottom-19

    def qr_code(self,url,x,y,size):
        widget=qr.QrCodeWidget(url,barFillColor=C['deep'],barBorder=2)
        box=widget.getBounds();scale=size/(box[2]-box[0]);d=Drawing(size,size,transform=[scale,0,0,scale,0,0]);d.add(widget)
        renderPDF.draw(d,self.c,x,y)

    def cover(self):
        self.page=1;self.key='cover';self.c.bookmarkPage('cover');self.c.addOutlineEntry('Manual do usuário','cover',level=0)
        self.c.setFillColor(C['deep']);self.c.rect(0,0,W,H,fill=1,stroke=0)
        self.c.setFillColor(C['white']);self.c.roundRect(M,H-101,201,56,13,fill=1,stroke=0)
        self.c.drawImage(str(BRAND/'aliancadx-logo.png'),M+15,H-88,width=171,height=40,mask='auto')
        self.label('GUIA COMPLETO  /  ACESSO REMOTO',M,H-143,size=9.2,color='neon')
        self.at('Manual do<br/>usuário',M,H-163,CW,size=43,leading=50,color='white',bold=True)
        self.at('Seu PC no Windows, Android,<br/>iPhone e iPad.',M,H-284,CW,size=18,leading=27,color='mint')
        self.at('Instale. Conecte. Autorize. Controle.<br/>Do primeiro acesso à transferência de arquivos.',M,H-359,CW,size=11.2,leading=18,color='mint')
        # A vector desktop and phone illustrate roles without claiming a device screenshot.
        x=M+9;y=230;dw=353;dh=167
        self.c.setFillColor(C['green']);self.c.roundRect(x,y,dw,dh,12,fill=1,stroke=0)
        self.c.setFillColor(C['surface']);self.c.roundRect(x+8,y+24,dw-16,dh-32,7,fill=1,stroke=0)
        self.c.setFillColor(C['deep']);self.c.rect(x+16,y+32,72,dh-48,fill=1,stroke=0)
        for n in range(3):self.c.setFillColor(C['green']);self.c.roundRect(x+102+n*71,y+77,59,47,5,fill=1,stroke=0)
        self.c.setFillColor(C['green']);self.c.rect(x+157,y-27,39,27,fill=1,stroke=0)
        self.c.roundRect(x+111,y-34,132,11,5,fill=1,stroke=0)
        px=M+348;py=211;pw=137;ph=222
        self.c.setFillColor(C['mint']);self.c.roundRect(px,py,pw,ph,17,fill=1,stroke=0)
        self.c.setFillColor(C['deep']);self.c.roundRect(px+6,py+7,pw-12,ph-14,12,fill=1,stroke=0)
        self.c.setFillColor(C['green']);self.c.roundRect(px+15,py+76,pw-30,101,6,fill=1,stroke=0)
        self.c.setStrokeColor(C['neon']);self.c.setLineWidth(2);self.c.circle(px+84,py+121,12,fill=0,stroke=1)
        self.label('SEU PC',px+26,py+145,size=12,color='white')
        self.pill('Conectado',px+17,py+39,w=pw-34,h=23,size=8)
        self.c.setFillColor(C['neon']);self.c.circle(M+14,176,3,fill=1,stroke=0)
        self.label('PASSO A PASSO  •  SUMÁRIO CLICÁVEL',M+27,173,size=9,color='mint')
        for i,(platform,version) in enumerate([('WINDOWS','7.1.0'),('ANDROID','7.1.0'),('IPHONE / IPAD','PWA 7.1.0')]):
            cx=M+i*(CW/3);self.label(platform,cx,112,size=8,color='mint');self.label(version,cx,87,size=17,color='white')
        self.c.setStrokeColor(C['green']);self.c.line(M,61,W-M,61)
        self.label('EDIÇÃO ATUALIZADA  /  27 DE OUTUBRO DE 2026',M,39,size=8.2,color='mint')
        self.y=180

    def contents(self):
        self.start('contents','NAVEGAÇÃO','Encontre o que precisa','Clique ou toque em um assunto. O botão Sumário está no rodapé das páginas.')
        groups=[('COMEÇAR',['quick','roles','downloads']),('WINDOWS',['win_install','host_ready','invite','win_connect','consent','win_controls','win_multi','win_files','saved']),('ANDROID',['android_install','android_origin','android_connect','android_controls','android_multi','android_files']),('IPHONE E IPAD',['ios_install','ios_pc','ios_offer','ios_answer','ios_controls','ios_multi_files']),('REDE E CUIDADOS',['network','stop','usb','maintenance']),('SE PRECISAR DE AJUDA',['connect_fix','install_fix','use_fix','ios_fix']),('CONSULTA E REFERÊNCIAS',['lan','identity','support','references'])]
        columns=[groups[:3],groups[3:]];colw=(CW-25)/2;top=self.y
        for ci,column in enumerate(columns):
            x=M+ci*(colw+25);y=top
            for heading,keys in column:
                self.label(heading,x,y,size=8.5);y-=18
                for key in keys:
                    title=self.content[key]['toc'] if 'toc' in self.content[key] else self.content[key]['title']
                    p,h=self.para_size(title,colw-27,size=9.35,leading=13.1)
                    p.drawOn(self.c,x,y-h);self.c.setFont('Bold',9);self.c.setFillColor(C['green']);self.c.drawRightString(x+colw,y-10,str(PAGES[key]).zfill(2))
                    self.c.linkRect('',key,(x,y-h-5,x+colw,y+2),relative=0,thickness=0)
                    y-=max(23,h+8)
                y-=13
            if y<BOTTOM:raise ValueError('TOC column overflow')
        self.y=min(y,self.y)

    def block(self,b):
        t=b['type']
        if t=='p':self.p(b['text'])
        elif t=='steps':self.steps(b['items'])
        elif t=='note':self.note(b['title'],b['text'],b.get('tone','info'))
        elif t=='table':self.table(b['headers'],b['rows'],b.get('widths'))
        elif t=='bullets':self.bullets(b['items'])
        elif t=='stats':self.stats(b['items'])
        elif t=='mock':self.mock(b['kind'])
        elif t=='link':self.link(b['label'],b['url'])
        else:raise ValueError('Unknown block '+t)

    def build(self):
        for key in ORDER:
            if key=='cover':self.cover();continue
            if key=='contents':self.contents();continue
            page=self.content[key];self.start(key,page['section'],page['title'],page['subtitle'])
            for b in page['blocks']:self.block(b)
            if key=='downloads':
                self.qr_code('https://perabru.github.io/AliancaDX-AcessoRemoto/',W-M-99,84,89)
                self.at('Abra a página de downloads<br/>pela câmera do celular.',M,133,CW-122,size=10.5,color='muted')
        self.finish();self.c.save()
        return self.metrics

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--metrics',type=Path)
    a=p.parse_args();fonts();content={}
    for source in ['content_core.json','content_windows.json','content_android.json','content_ios.json']:
        for page in json.loads((HERE/source).read_text()):
            if page['key'] in content:raise ValueError('Duplicate page '+page['key'])
            content[page['key']]=page
    missing=set(ORDER)-{'cover','contents'}-set(content)
    if missing:raise ValueError('Missing pages: '+repr(missing))
    a.output.parent.mkdir(parents=True,exist_ok=True);metrics=Manual(a.output,content).build()
    if a.metrics:a.metrics.write_text(json.dumps(metrics,indent=2))
    print(json.dumps({'pdf':str(a.output),'pages':len(metrics),'bytes':a.output.stat().st_size}))

if __name__=='__main__':main()
