export function validPosition(lat, lon) {
  return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
}
export function freshPosition(position, now=Date.now()) {
  return position && !position.fixed && Number.isFinite(position.updated) && now >= position.updated && now-position.updated < 60000;
}
export function fixedLocation(value, source='Manually set') {
  if (!value || !validPosition(value.lat,value.lon)) return null;
  return {lat:value.lat,lon:value.lon,accuracy:null,updated:null,fixed:true,source};
}
export function usableOrigin(position, now=Date.now()) {
  return Boolean(position && validPosition(position.lat,position.lon) && (position.fixed===true || freshPosition(position,now)));
}
export function drivingURL(destination, origin=null, now=Date.now()) {
  if (!destination || !validPosition(destination.lat,destination.lon)) return null;
  const url=new URL('https://www.google.com/maps/dir/');
  url.searchParams.set('api','1');
  url.searchParams.set('destination',`${destination.lat},${destination.lon}`);
  url.searchParams.set('travelmode','driving');
  if (usableOrigin(origin,now)) url.searchParams.set('origin',`${origin.lat},${origin.lon}`);
  return url.href;
}
