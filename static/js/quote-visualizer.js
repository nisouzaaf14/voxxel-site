(function(){
  const canvas = document.getElementById('quote-visualizer');
  if(!canvas) return;
  const ctx = canvas.getContext('2d', {alpha:true});
  let state = {altura:10, largura:10, profundidade:10, material:'pla'};
  let frame = 0;
  let dpr = Math.min(window.devicePixelRatio || 1, 2);
  let particles = [];

  const materialHue = {pla:270, petg:285, abs:258, resina:300};

  function resize(){
    const r = canvas.getBoundingClientRect();
    if(!r.width || !r.height) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(r.width*dpr);
    canvas.height = Math.round(r.height*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    particles = Array.from({length:42},(_,i)=>({
      x:Math.random()*r.width,
      y:Math.random()*r.height,
      z:Math.random(),
      s:.6+Math.random()*1.4,
      phase:Math.random()*Math.PI*2,
      drift:.15+Math.random()*.35
    }));
  }

  function project(p, cx, cy, scale, ay, ax){
    let x=p.x, y=p.y, z=p.z;
    const cosy=Math.cos(ay), siny=Math.sin(ay);
    const x1=x*cosy-z*siny, z1=x*siny+z*cosy;
    const cosx=Math.cos(ax), sinx=Math.sin(ax);
    const y1=y*cosx-z1*sinx, z2=y*sinx+z1*cosx;
    const perspective=1/(1+z2*.16);
    return {x:cx+x1*scale*perspective,y:cy-y1*scale*perspective,z:z2};
  }

  function boxPoints(w,h,d){
    const pts=[];
    const density=5;
    for(let i=0;i<=density;i++){
      for(let j=0;j<=density;j++){
        const a=i/density-.5, b=j/density-.5;
        pts.push({x:a*w,y:b*h,z:-d/2},{x:a*w,y:b*h,z:d/2});
        pts.push({x:-w/2,y:a*h,z:b*d},{x:w/2,y:a*h,z:b*d});
        pts.push({x:a*w,y:-h/2,z:b*d},{x:a*w,y:h/2,z:b*d});
      }
    }
    return pts;
  }

  function edgePairs(w,h,d){
    const v=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]].map(([x,y,z])=>({x:x*w/2,y:y*h/2,z:z*d/2}));
    const e=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
    return {v,e};
  }

  function draw(){
    const r=canvas.getBoundingClientRect();
    const w=r.width,h=r.height;
    ctx.clearRect(0,0,w,h);
    frame += .008;
    const hue=materialHue[state.material] || 270;

    // Floating depth particles, reminiscent of a point-cloud scanner.
    particles.forEach((p,idx)=>{
      p.y -= p.drift*.12;
      p.x += Math.sin(frame*1.7+p.phase)*.05;
      if(p.y < -4){p.y=h+4;p.x=Math.random()*w}
      const alpha=.08+p.z*.22;
      ctx.beginPath();
      ctx.fillStyle=`hsla(${hue}, 72%, 74%, ${alpha})`;
      ctx.arc(p.x,p.y,p.s*(.7+p.z),0,Math.PI*2);ctx.fill();
      if(idx%8===0){
        ctx.beginPath();ctx.strokeStyle=`hsla(${hue},65%,65%,${alpha*.22})`;ctx.moveTo(p.x-7,p.y);ctx.lineTo(p.x+7,p.y);ctx.stroke();
      }
    });

    const A=Math.max(state.altura||1,.1), W=Math.max(state.largura||1,.1), D=Math.max(state.profundidade||1,.1);
    const max=Math.max(A,W,D,1);
    const bw=.75*W/max+.42, bh=.75*A/max+.42, bd=.75*D/max+.42;
    const scale=Math.min(w,h)*.27;
    const cx=w*.5, cy=h*.48;
    const ay=.62+Math.sin(frame*.42)*.08, ax=-.42+Math.sin(frame*.33)*.025;
    const cloud=boxPoints(bw,bh,bd).map(p=>project(p,cx,cy,scale,ay,ax)).sort((a,b)=>a.z-b.z);

    // Soft interior haze.
    const g=ctx.createRadialGradient(cx,cy,8,cx,cy,scale*.9);
    g.addColorStop(0,`hsla(${hue},72%,62%,.12)`);g.addColorStop(1,`hsla(${hue},72%,62%,0)`);
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,scale*.9,0,Math.PI*2);ctx.fill();

    cloud.forEach((p,i)=>{
      const twinkle=.68+.32*Math.sin(frame*3+i*.37);
      const depth=Math.max(.18,Math.min(1,(p.z+1.4)/2.8));
      ctx.beginPath();ctx.fillStyle=`hsla(${hue},82%,${67+depth*15}%,${(.18+depth*.58)*twinkle})`;
      ctx.arc(p.x,p.y,.65+depth*1.05,0,Math.PI*2);ctx.fill();
    });

    // Principal wireframe edges.
    const {v,e}=edgePairs(bw,bh,bd);
    const pv=v.map(p=>project(p,cx,cy,scale,ay,ax));
    ctx.lineWidth=1;
    e.forEach(([a,b],i)=>{
      const depth=(pv[a].z+pv[b].z)/2;
      const alpha=.16+Math.max(0,Math.min(.46,(depth+1.2)*.18));
      ctx.beginPath();ctx.strokeStyle=`hsla(${hue},78%,76%,${alpha})`;ctx.moveTo(pv[a].x,pv[a].y);ctx.lineTo(pv[b].x,pv[b].y);ctx.stroke();
    });

    // Scan line.
    const scan=((Math.sin(frame*1.25)+1)/2);
    const sy=cy-scale*.65+scan*scale*1.3;
    const grad=ctx.createLinearGradient(cx-scale,sy,cx+scale,sy);
    grad.addColorStop(0,'transparent');grad.addColorStop(.5,`hsla(${hue},95%,80%,.42)`);grad.addColorStop(1,'transparent');
    ctx.strokeStyle=grad;ctx.lineWidth=.8;ctx.beginPath();ctx.moveTo(cx-scale*.85,sy);ctx.lineTo(cx+scale*.85,sy);ctx.stroke();

    requestAnimationFrame(draw);
  }

  window.VoxxelQuoteVisualizer={update(next){state={...state,...next}}};
  window.addEventListener('resize',resize,{passive:true});
  resize(); draw();
})();
