// Localisation stays offline and keeps the existing slide URLs stable.
let language='et';
try { language=localStorage.getItem('openmapstack-talk-language')==='en'?'en':'et'; } catch {}
const tr=(et,en)=>language==='en'?en:et;
function updateFullscreenLabel(){
 const on=Boolean(document.fullscreenElement),button=$('#fullscreen');
 button.textContent=on?tr('Välju täisekraanist','Exit fullscreen'):tr('Täisekraan','Fullscreen');
 button.dataset.compactLabel=on?tr('Välju','Exit'):tr('Täisekraan','Fullscreen');
 button.setAttribute('aria-label',on?tr('Välju täisekraanist','Exit fullscreen'):tr('Lülita täisekraan','Toggle fullscreen'));
}
function updateChrome(){
 document.documentElement.lang=language;
 document.querySelector('meta[name="description"]').content=tr('OpenMapStack: 45 minuti demo ja esitlus GIS-ekspertidele eesti ja inglise keeles.','OpenMapStack: a 45-minute demo and presentation for GIS experts in Estonian and English.');
 const textEntries=[
  ['#choose','Vali slaid','Choose slide'],['#next','Edasi →','Next →'],['#notes-toggle','Märkmed','Notes'],
  ['#chooser-title','Vali slaid','Choose slide'],['#notes .dialog-head h3','Esinejamärkmed','Speaker notes'],
  ['[data-close="lightbox"]','Sulge','Close'],
  ['#chooser .caption','24 slaidi · 45 minutit · ← → liikumiseks · N märkmeteks · F täisekraaniks','24 slides · 45 minutes · ← → navigate · N notes · F fullscreen']
 ];
 for(const [selector,et,en] of textEntries)$(selector).textContent=tr(et,en);
 const ariaEntries=[
  ['#stage','Esitluse slaid','Presentation slide'],['footer','Esitluse juhtnupud','Presentation controls'],
  ['#previous','Eelmine slaid','Previous slide'],['#notes-toggle','Näita esinejamärkmeid','Show speaker notes'],
  ['[data-close="chooser"]','Sulge slaidivalik','Close slide chooser'],['#notes','Esinejamärkmed','Speaker notes'],
  ['#close-notes','Sulge märkmed','Close notes'],['[data-close="lightbox"]','Sulge ekraanipilt','Close screenshot'],
  ['.language-switch','Esitluse keel','Presentation language']
 ];
 for(const [selector,et,en] of ariaEntries)$(selector).setAttribute('aria-label',tr(et,en));
 for(const button of document.querySelectorAll('[data-language]'))button.setAttribute('aria-pressed',String(button.dataset.language===language));
 updateFullscreenLabel();
}
function setLanguage(next){
 if(!['et','en'].includes(next)||next===language)return;
 language=next;SLIDES=language==='en'?SLIDES_EN:SLIDES_ET;
 try { localStorage.setItem('openmapstack-talk-language',language); } catch {}
 $('#toast').hidden=true;render();
}
